
import collections
import os.path
import json
import sys

from PyQt5.QtCore import pyqtSignal, QObject

class QJSONObj(QObject):
    changed = pyqtSignal()

    def __init__(self, data=None, **kw):
        super().__init__()

        self.__dict__.update(kw)
        self._root = data if data else self._init()
        self.changed.connect(self.onChanged)
        self.changed.emit()

    @property
    def root(self):
        return self._root

    def prepare(self, root):
        pass

    def save(self):
        pass

    def _init(self):
        root = {}
        self.prepare(root)
        return root

    def onChanged(self):
        self.save()

    def __str__(self):
        return json.dumps(self.root)

    def serialize(self, fd):
        return json.dump(self.root, fd, indent=4)

    def dump(self):
        self.serialize(sys.stdout)

class QJSONFile(QJSONObj):
    def __init__(self, path):
        self._path = path

        super(QJSONFile, self).__init__()

    def _init(self):
        try:
            root = json.load(open(self.path, 'rt'))
        except Exception as e:
            root = collections.OrderedDict()

        self.prepare(root)
        return root

    @property
    def path(self):
        return self._path

    def save(self):
        with open(self.path, 'w+t') as fd:
            self.serialize(fd)
