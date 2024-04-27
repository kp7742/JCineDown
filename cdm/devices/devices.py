from utils import scriptsDir, joinPath, realPath


def getDevicePath(name):
    return realPath(joinPath(scriptsDir, f"cdm/devices/{name}", f"{name}.wvd"))


# Working
device_samsung_sm_g935f = getDevicePath("samsung_sm-g935f")
