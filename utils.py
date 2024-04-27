import os
import json

# Some functions
joinPath = os.path.join
isDir = os.path.isdir
isExist = os.path.exists
scriptsDir = os.path.dirname(__file__)
realPath = os.path.realpath


# Simple Implementation class to manage JSON Config Objects
class JSO:
    __jso = {}
    __path = ""
    __ind = None

    def __init__(self, path, indent=None):
        self.__path = path
        self.__ind = indent
        self.load()

    def store(self):
        try:
            fo = open(self.__path, "w")
            fo.write(json.dumps(self.__jso, indent=self.__ind))
            fo.close()
        except Exception as e:
            print(e)
            exit(0)

    def load(self):
        try:
            fo = open(self.__path, "r")
            self.__jso = json.load(fo)
            fo.close()
        except Exception as e:
            print(e)
            exit(0)

    def get(self, attr):
        return self.__jso[attr]

    def set(self, attr, val):
        self.__jso[attr] = val
        self.store()


def readFile(fname):
    f = open(fname, "r")
    data = f.read()
    f.close()
    return data


def outFile(fname, data):
    fo = open(fname, "w")
    fo.write(data)
    fo.close()


def copyFile(old, new):
    f = open(old, "r")
    outFile(new, f.read())
    f.close()


def clearFolder(dirs):
    filesToRemove = [joinPath(dirs, f) for f in os.listdir(dirs)]
    for f in filesToRemove:
        os.remove(f)


def createDir(path, override=False):
    if override or not isExist(path):
        os.mkdir(path)
        return True
    return False
