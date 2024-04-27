import os
import time
import base64

from Cryptodome.Cipher import PKCS1_OAEP, AES
from Cryptodome.Hash import CMAC, SHA256, HMAC, SHA1
from Cryptodome.PublicKey import RSA
from Cryptodome.Random import get_random_bytes
from Cryptodome.Random import random
from Cryptodome.Signature import pss
from Cryptodome.Util import Padding
from google.protobuf.message import DecodeError

from cdm.key import Key
from cdm.session import Session
from cdm.formats import wv_proto2_pb2 as wv_proto2


class Cdm:
    def __init__(self):
        self.sessions = {}
        self.raw_pssh = False

    def open_session(self, init_data_b64, device, raw_init_data=None, offline=False):
        # opening new cdm session
        if device.session_id_type == 'android':
            # format: 16 random hexdigits, 2 digit counter, 14 0s
            rand_ascii = ''.join(random.choice('ABCDEF0123456789') for _ in range(16))
            counter = '01'  # this resets regularly so it's fine to use 01
            rest = '00000000000000'
            session_id = rand_ascii + counter + rest
            session_id = session_id.encode('ascii')
        elif device.session_id_type == 'chrome':
            rand_bytes = get_random_bytes(16)
            session_id = rand_bytes
        else:
            # other formats NYI
            print("device type is unusable")
            return 1

        if raw_init_data and isinstance(raw_init_data, (bytes, bytearray)):
            # used for NF key exchange, where they don't provide a valid PSSH
            init_data = raw_init_data
            self.raw_pssh = True
        else:
            init_data = self._parse_init_data(init_data_b64)

        if init_data:
            new_session = Session(session_id, init_data, device, offline)
        else:
            print('unable to parse init data')
            return 1

        self.sessions[session_id] = new_session
        # session opened and init data parsed successfully
        return session_id

    @staticmethod
    def _parse_init_data(init_data_b64):
        parsed_init_data = wv_proto2.WidevineCencHeader()
        try:
            # trying to parse init_data directly
            parsed_init_data.ParseFromString(base64.b64decode(init_data_b64)[32:])
        except DecodeError:
            # unable to parse as-is, trying with removed pssh box header
            try:
                parsed_init_data.ParseFromString(base64.b64decode(init_data_b64)[32:])
            except DecodeError:
                print('unable to parse, unsupported init data format')
                return None
        return parsed_init_data

    def close_session(self, session_id):
        # closing cdm session
        if session_id in self.sessions:
            self.sessions.pop(session_id)
            # cdm session closed
            return 0
        else:
            print("session {} not found".format(session_id))
            return 1

    def set_service_certificate(self, session_id, cert_b64):
        # setting service certificate
        if session_id not in self.sessions:
            print("session id doesn't exist")
            return 1

        session = self.sessions[session_id]

        message = wv_proto2.SignedMessage()

        try:
            message.ParseFromString(base64.b64decode(cert_b64))
        except DecodeError:
            print("failed to parse cert as SignedMessage")

        service_certificate = wv_proto2.SignedDeviceCertificate()

        if message.Type:
            # "service cert provided as signed message"
            try:
                service_certificate.ParseFromString(message.Msg)
            except DecodeError:
                print("failed to parse service certificate")
                return 1
        else:
            # "service cert provided as signed device certificate"
            try:
                service_certificate.ParseFromString(base64.b64decode(cert_b64))
            except DecodeError:
                print("failed to parse service certificate")
                return 1

        session.service_certificate = service_certificate
        session.privacy_mode = True

        return 0

    def get_license_request(self, session_id):
        # getting license request

        if session_id not in self.sessions:
            print("session ID does not exist")
            return 1

        session = self.sessions[session_id]

        # raw pssh will be treated as bytes and not parsed
        if self.raw_pssh:
            license_request = wv_proto2.SignedLicenseRequestRaw()
        else:
            license_request = wv_proto2.SignedLicenseRequest()
        client_id = wv_proto2.ClientIdentification()

        if not os.path.exists(session.device_config.device_client_id_blob_filename):
            print("no client ID blob available for this device")
            return 1

        with open(session.device_config.device_client_id_blob_filename, "rb") as f:
            try:
                client_id.ParseFromString(f.read())
            except DecodeError:
                print("client id failed to parse as protobuf")
                return 1

        # building license request
        if not self.raw_pssh:
            license_request.Type = wv_proto2.SignedLicenseRequest.MessageType.Value('LICENSE_REQUEST')
            license_request.Msg.ContentId.CencId.Pssh.CopyFrom(session.init_data)
        else:
            license_request.Type = wv_proto2.SignedLicenseRequestRaw.MessageType.Value('LICENSE_REQUEST')
            license_request.Msg.ContentId.CencId.Pssh = session.init_data  # bytes

        if session.offline:
            license_type = wv_proto2.LicenseType.Value('OFFLINE')
        else:
            license_type = wv_proto2.LicenseType.Value('DEFAULT')

        license_request.Msg.ContentId.CencId.LicenseType = license_type
        license_request.Msg.ContentId.CencId.RequestId = session_id
        license_request.Msg.Type = wv_proto2.LicenseRequest.RequestType.Value('NEW')
        license_request.Msg.RequestTime = int(time.time())
        license_request.Msg.ProtocolVersion = wv_proto2.ProtocolVersion.Value('CURRENT')

        if session.device_config.send_key_control_nonce:
            license_request.Msg.KeyControlNonce = random.randrange(1, 2 ** 31)

        if session.privacy_mode:
            if session.device_config.vmp:
                # vmp required, adding to client_id
                # reading vmp hashes
                vmp_hashes = wv_proto2.FileHashes()
                with open(session.device_config.device_vmp_blob_filename, "rb") as f:
                    try:
                        vmp_bytes = vmp_hashes.ParseFromString(f.read())
                    except DecodeError:
                        print("vmp hashes failed to parse as protobuf")
                        return 1
                client_id._FileHashes.CopyFrom(vmp_hashes)

            # privacy mode & service certificate loaded, encrypting client id
            cid_aes_key = get_random_bytes(16)
            cid_iv = get_random_bytes(16)

            cid_cipher = AES.new(cid_aes_key, AES.MODE_CBC, cid_iv)

            encrypted_client_id = cid_cipher.encrypt(Padding.pad(client_id.SerializeToString(), 16))

            service_public_key = RSA.importKey(session.service_certificate._DeviceCertificate.PublicKey)

            service_cipher = PKCS1_OAEP.new(service_public_key)

            encrypted_cid_key = service_cipher.encrypt(cid_aes_key)

            encrypted_client_id_proto = wv_proto2.EncryptedClientIdentification()

            encrypted_client_id_proto.ServiceId = session.service_certificate._DeviceCertificate.ServiceId
            encrypted_client_id_proto.ServiceCertificateSerialNumber = session.service_certificate._DeviceCertificate.SerialNumber
            encrypted_client_id_proto.EncryptedClientId = encrypted_client_id
            encrypted_client_id_proto.EncryptedClientIdIv = cid_iv
            encrypted_client_id_proto.EncryptedPrivacyKey = encrypted_cid_key

            license_request.Msg.EncryptedClientId.CopyFrom(encrypted_client_id_proto)
        else:
            license_request.Msg.ClientId.CopyFrom(client_id)

        if session.device_config.private_key_available:
            key = RSA.importKey(open(session.device_config.device_private_key_filename).read())
            session.device_key = key
        else:
            print("need device private key, other methods unimplemented")
            return 1

        # signing license request
        _hash = SHA1.new(license_request.Msg.SerializeToString())

        signature = pss.new(key).sign(_hash)

        license_request.Signature = signature

        session.license_request = license_request

        # license request created
        return license_request.SerializeToString()

    def provide_license(self, session_id, license_data):
        # decrypting provided license
        if session_id not in self.sessions:
            print("session does not exist")
            return 1

        session = self.sessions[session_id]

        if not session.license_request:
            print("generate a license request first!")
            return 1

        _license = wv_proto2.SignedLicense()
        try:
            _license.ParseFromString(license_data)
        except DecodeError:
            print("unable to parse license - check protobufs")
            return 1

        session.license = _license

        # deriving keys from session key
        oaep_cipher = PKCS1_OAEP.new(session.device_key)

        session.session_key = oaep_cipher.decrypt(_license.SessionKey)

        lic_req_msg = session.license_request.Msg.SerializeToString()

        enc_key_base = b"ENCRYPTION\000" + lic_req_msg + b"\0\0\0\x80"
        auth_key_base = b"AUTHENTICATION\0" + lic_req_msg + b"\0\0\2\0"

        enc_key = b"\x01" + enc_key_base
        auth_key_1 = b"\x01" + auth_key_base
        auth_key_2 = b"\x02" + auth_key_base

        cmac_obj = CMAC.new(session.session_key, ciphermod=AES)
        cmac_obj.update(enc_key)

        enc_cmac_key = cmac_obj.digest()

        cmac_obj = CMAC.new(session.session_key, ciphermod=AES)
        cmac_obj.update(auth_key_1)
        auth_cmac_key_1 = cmac_obj.digest()

        cmac_obj = CMAC.new(session.session_key, ciphermod=AES)
        cmac_obj.update(auth_key_2)
        auth_cmac_key_2 = cmac_obj.digest()

        auth_cmac_combined_1 = auth_cmac_key_1 + auth_cmac_key_2

        session.derived_keys['enc'] = enc_cmac_key
        session.derived_keys['auth'] = auth_cmac_combined_1

        # verifying license signature
        lic_hmac = HMAC.new(session.derived_keys['auth'], digestmod=SHA256)
        lic_hmac.update(_license.Msg.SerializeToString())

        if lic_hmac.digest() != _license.Signature:
            # license signature doesn't match - writing bin, so they can be debugged
            print("license signature doesn't match")

        for key in _license.Msg.Key:
            if key.Id:
                key_id = key.Id
            else:
                key_id = wv_proto2.License.KeyContainer.KeyType.Name(key.Type).encode('utf-8')
            encrypted_key = key.Key
            iv = key.Iv
            _type = wv_proto2.License.KeyContainer.KeyType.Name(key.Type)

            cipher = AES.new(session.derived_keys['enc'], AES.MODE_CBC, iv=iv)
            decrypted_key = cipher.decrypt(encrypted_key)
            if _type == "OPERATOR_SESSION":
                permissions = []
                perms = key._OperatorSessionKeyPermissions
                for (descriptor, value) in perms.ListFields():
                    if value == 1:
                        permissions.append(descriptor.name)
                print(permissions)
            else:
                permissions = []
            session.keys.append(Key(key_id, _type, Padding.unpad(decrypted_key, 16), permissions))

        # decrypted all keys
        return 0

    def get_keys(self, session_id):
        if session_id in self.sessions:
            return self.sessions[session_id].keys
        else:
            print("session not found")
            return 1
