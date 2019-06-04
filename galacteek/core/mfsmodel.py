import os.path

from PyQt5.QtWidgets import QApplication

from PyQt5.QtGui import QStandardItemModel

from PyQt5.QtCore import QUrl
from PyQt5.QtCore import Qt
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtCore import QMimeData
from PyQt5.QtCore import QDir
from PyQt5.QtCore import QFile
from PyQt5.QtCore import QModelIndex

from galacteek.ipfs.cidhelpers import joinIpfs
from galacteek.ipfs.cidhelpers import IPFSPath
from galacteek.ipfs.ipfsops import *
from galacteek.ipfs.wrappers import ipfsOp
from galacteek.appsettings import *

from galacteek.core.modelhelpers import *
from galacteek.ui.i18n import *  # noqa
from galacteek.ui.helpers import *


def sampleQrCodes():
    dir = QDir(':/share/qr-codes')
    if dir.exists():
        for entry in dir.entryList():
            yield entry, QFile(':/share/qr-codes/{}'.format(entry))


class MFSItem(UneditableItem):
    def __init__(self, text, path=None, parenthash=None, icon=None):
        super(MFSItem, self).__init__(text, icon=icon)
        self._path = path
        self._parentHash = parenthash

    @property
    def path(self):
        return self._path

    @path.setter
    def path(self, v):
        self._path = v

    @property
    def parentHash(self):
        return self._parentHash

    def setParentHash(self, pHash):
        self._parentHash = pHash

    def childrenItems(self):
        for row in range(0, self.rowCount()):
            child = self.child(row, 0)
            if child:
                yield child

    def findChildByMultihash(self, multihash):
        for item in self.childrenItems():
            if not isinstance(item, MFSNameItem):
                continue
            if item.entry['Hash'] == multihash:
                return item

    def findChildByName(self, name):
        for item in self.childrenItems():
            if not isinstance(item, MFSNameItem):
                continue
            if item.entry['Name'] == name:
                return item


class MFSRootItem(MFSItem):
    expandedItemsCount = 0


class MFSNameItem(MFSItem):
    def __init__(self, entry, text, icon):
        super().__init__(text, icon=icon)

        self._entry = entry
        self._mimeType = None

    @property
    def entry(self):
        return self._entry

    @property
    def ipfsPath(self):
        return IPFSPath(self.fullPath)

    @property
    def dwebUrl(self):
        return QUrl('dweb:{}'.format(self.fullPath))

    @property
    def mimeType(self):
        return self._mimeType

    def mimeFromDb(self, db):
        self._mimeType = db.mimeTypeForFile(self.entry['Name'])

    @property
    def mimeCategory(self):
        if self.mimeType:
            return self.mimeTypeName.split('/')[0]

    @property
    def mimeTypeName(self):
        if self.mimeType:
            return self.mimeType.name()

    @property
    def fullPath(self):
        parentHash = self.parentHash
        if parentHash:
            return joinIpfs(os.path.join(parentHash, self.entry['Name']))
        else:
            return joinIpfs(self.entry['Hash'])

    def isFile(self):
        return self.entry['Type'] == 0

    def isDir(self):
        return self.entry['Type'] == 1


class MFSItemModel(QStandardItemModel):
    # Drag and drop events
    fileDropEvent = pyqtSignal(str)
    directoryDropEvent = pyqtSignal(str)

    # An item's view was refreshed
    refreshed = pyqtSignal(MFSNameItem)

    needExpand = pyqtSignal(QModelIndex)

    def __init__(self):
        QStandardItemModel.__init__(self)
        self.app = QApplication.instance()

        self.itemRoot = self.invisibleRootItem()
        self.itemRootIdx = self.indexFromItem(self.itemRoot)
        self.initialized = False
        self.qrInitialized = False

    def setupItemsFromProfile(self, profile):
        self.itemHome = MFSRootItem(iHome(), path=profile.pathHome)
        self.itemPictures = MFSRootItem(iPictures(),
                                        path=profile.pathPictures)
        self.itemVideos = MFSRootItem(iVideos(),
                                      path=profile.pathVideos)
        self.itemMusic = MFSRootItem(iMusic(),
                                     path=profile.pathMusic)
        self.itemCode = MFSRootItem(iCode(),
                                    path=profile.pathCode)
        self.itemDocuments = MFSRootItem(iDocuments(),
                                         path=profile.pathDocuments)
        self.itemWebPages = MFSRootItem(iWebPages(),
                                        path=profile.pathWebPages)
        self.itemDWebApps = MFSRootItem(iDWebApps(),
                                        path=profile.pathDWebApps)
        self.itemQrCodes = MFSRootItem(iQrCodes(),
                                       path=profile.pathQrCodes)

        self.itemRoot.appendRows([
            self.itemHome,
            self.itemPictures,
            self.itemVideos,
            self.itemCode,
            self.itemMusic,
            self.itemDocuments,
            self.itemWebPages,
            self.itemDWebApps,
            self.itemQrCodes
        ])

        self.initialized = True

    @ipfsOp
    async def setupQrCodesFolder(self, ipfsop, profile, model, files):
        """
        Import the sample QR codes to the corresponding folder
        Needs rewrite ..
        """
        for name, qrFile in sampleQrCodes():
            await ipfsop.sleep()

            if qrFile.exists():
                path = self.app.tempDir.filePath(name)
                if qrFile.copy(path):
                    tmpFile = QFile(path)
                    entry = await ipfsop.addPath(path)
                    await ipfsop.sleep()

                    if not entry:
                        tmpFile.remove()
                        continue

                    exists = await ipfsop.filesLookupHash(
                        model.itemQrCodes.path, entry['Hash'])

                    await ipfsop.sleep()

                    if not exists:
                        await files.linkEntry(ipfsop, entry,
                                              model.itemQrCodes.path,
                                              entry['Name'])

                    tmpFile.remove()

        self.qrInitialized = True

    def displayItem(self, arg):
        self.itemRoot.appendRow(arg)

    def supportedDropActions(self):
        return Qt.CopyAction | Qt.MoveAction | \
            Qt.TargetMoveAction | Qt.LinkAction

    def mimeData(self, indexes):
        mimedata = QMimeData()

        for idx in indexes:
            if not idx.isValid():
                continue

            nameItem = self.getNameItemFromIdx(idx)

            if nameItem:
                mimedata.setUrls([nameItem.dwebUrl])
                break

        return mimedata

    def canDropMimeData(self, data, action, row, column, parent):
        mimeText = data.text()

        if mimeText and mimeText.startswith('file://'):
            return True
        return False

    def dropMimeData(self, data, action, row, column, parent):
        if data.hasUrls():
            for url in data.urls():
                path = url.toLocalFile()
                if os.path.isfile(path):
                    self.fileDropEvent.emit(path)
                if os.path.isdir(path):
                    self.directoryDropEvent.emit(path)
            return True
        else:
            return False

    def getHashFromIdx(self, idx):
        idxHash = self.index(idx.row(), 2, idx.parent())
        return self.data(idxHash)

    def getNameFromIdx(self, idx):
        idxName = self.index(idx.row(), 0, idx.parent())
        return self.data(idxName)

    def getNameItemFromIdx(self, idx):
        idxName = self.index(idx.row(), 0, idx.parent())
        return self.itemFromIndex(idxName)


def createMFSModel():
    # Setup the model
    model = MFSItemModel()
    model.setHorizontalHeaderLabels(
        [iFileName(), iFileSize(), iMultihash()])
    return model