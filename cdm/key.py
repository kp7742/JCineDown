import binascii


class Key:
    def __init__(self, kid, type_, key, permissions=None):
        if permissions is None:
            permissions = []
        self.kid = kid
        self.type = type_
        self.key = key
        self.permissions = permissions

    def __repr__(self):
        if self.type == "OPERATOR_SESSION":
            return "key(kid={}, type={}, key={}, permissions={})".format(self.kid, self.type,
                                                                         binascii.hexlify(self.key), self.permissions)
        else:
            return "key(kid={}, type={}, key={})".format(self.kid, self.type, binascii.hexlify(self.key))
