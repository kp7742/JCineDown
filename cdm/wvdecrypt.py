from pywidevine.cdm import Cdm
from pywidevine.device import Device
from pywidevine.pssh import PSSH


class WvDecrypt(object):
    def __init__(self, device, cert_data_b64):
        self.device = Device.load(device)
        self.cdm = Cdm.from_device(self.device)
        self.session = self.cdm.open()
        if cert_data_b64:
            self.cdm.set_service_certificate(self.session, cert_data_b64)

    def get_keys(self):
        keys_wvDecrypt = {}
        try:
            for key in self.cdm.get_keys(self.session):
                if key.type == 'CONTENT':
                    keys_wvDecrypt[key.kid.hex] = key.key.hex()
        except Exception:
            return keys_wvDecrypt
        return keys_wvDecrypt

    def get_challenge(self, pssh_b64):
        return self.cdm.get_license_challenge(self.session, PSSH(pssh_b64))

    def update_license(self, _license):
        self.cdm.parse_license(self.session, _license)

    def close(self):
        self.cdm.close(self.session)
