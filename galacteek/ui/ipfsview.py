import os.path
import functools

from PyQt5.QtWidgets import QApplication
from PyQt5.QtWidgets import QWidget
from PyQt5.QtWidgets import QLabel
from PyQt5.QtWidgets import QPushButton
from PyQt5.QtWidgets import QVBoxLayout
from PyQt5.QtWidgets import QHBoxLayout
from PyQt5.QtWidgets import QListView
from PyQt5.QtWidgets import QToolBox
from PyQt5.QtWidgets import QProgressBar
from PyQt5.QtWidgets import QSpacerItem
from PyQt5.QtWidgets import QSizePolicy
from PyQt5.QtWidgets import QToolButton
from PyQt5.QtWidgets import QAbstractItemView
from PyQt5.QtWidgets import QStackedWidget

from PyQt5.QtGui import QKeySequence

from PyQt5.QtCore import QAbstractListModel
from PyQt5.QtCore import QModelIndex
from PyQt5.QtCore import QCoreApplication
from PyQt5.QtCore import Qt
from PyQt5.QtCore import QEvent
from PyQt5.QtCore import QObject
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtCore import QMimeData
from PyQt5.QtCore import QUrl
from PyQt5.QtCore import QVariant

from galacteek.appsettings import *
from galacteek.ipfs import cidhelpers
from galacteek.ipfs.stat import StatInfo
from galacteek.ipfs.ipfsops import *
from galacteek.ipfs.wrappers import ipfsOp, ipfsStatOp
from galacteek.ipfs.cidhelpers import IPFSPath
from galacteek.ipfs.cidhelpers import joinIpfs
from galacteek.ipfs.cidhelpers import cidValid
from galacteek.ipfs.cidhelpers import cidConvertBase32
from galacteek import ensure
from galacteek import partialEnsure

from .clipboard import iCopyCIDToClipboard
from .clipboard import iCopyPathToClipboard
from .clipboard import iCopyPubGwUrlToClipboard

from .hashmarks import *  # noqa
from .clips import RotatingCubeRedFlash140d
from .i18n import *
from .helpers import *
from .widgets import AnimatedLabel
from .widgets import GalacteekTab
from .widgets import IPFSUrlLabel
from .widgets import IPFSPathClipboardButton
from .widgets import HashmarkThisButton

import aioipfs


def iCIDInfo(cidv, linksCount, size):
    return QCoreApplication.translate(
        'IPFSHashExplorer',
        'CID (v{0}): {1} links, total size: {2}').format(
        cidv,
        linksCount,
        size)


def iGitClone():
    return QCoreApplication.translate('IPFSHashExplorer',
                                      'Clone repository')


def iGitClonedRepo(path):
    return QCoreApplication.translate(
        'IPFSHashExplorer',
        'Cloned git repository: {0}').format(path)


def iGitErrorCloning(msg):
    return QCoreApplication.translate(
        'IPFSHashExplorer',
        'Error cloning git repository: {0}').format(msg)


def iGitInvalid():
    return QCoreApplication.translate('IPFSHashExplorer',
                                      'Invalid git repository')


def iLoading():
    return QCoreApplication.translate('IPFSHashExplorer', 'Loading ...')


def iLoadedEntries(count, persec):
    return QCoreApplication.translate(
        'IPFSHashExplorer',
        'Loaded entries: <b>{0}</b> (~{1} entries/s)').format(count, persec)


def iTimeout():
    return QCoreApplication.translate('IPFSHashExplorer',
                                      'Timeout error')


def iTimeoutTryNoResolve():
    return QCoreApplication.translate(
        'IPFSHashExplorer',
        'Timeout error: trying without resolving nodes types ..')


def iTimeoutInvalidHash():
    return QCoreApplication.translate('IPFSHashExplorer',
                                      'Timeout error: invalid hash')


def iUnixFSFileToolTip(eInfo):
    return QCoreApplication.translate(
        'IPFSHashExplorer',
        '''
        <p><b>{0}</b></p>
        <p>MIME type: {1}</p>
        <p>CID: {2}</p>
        <p>Size: {3}</p>
        ''').format(
            eInfo.filename,
            eInfo.mimeType,
            eInfo.cid,
            sizeFormat(eInfo.entry['Size'])
    )


class EntryInfo:
    def __init__(self, entry, parentCid):
        self._entry = entry
        self._parentCid = parentCid

    @property
    def entry(self):
        return self._entry

    @property
    def filename(self):
        return self.entry['Name']

    @property
    def ipfsPath(self):
        return IPFSPath(self.getFullPath(), autoCidConv=True)

    @property
    def mimeType(self):
        return self._mimeType

    @mimeType.setter
    def mimeType(self, mime):
        self._mimeType = mime

    def mimeFromDb(self, db):
        mType = db.mimeTypeForFile(self.entry['Name'])
        if mType:
            self.mimeType = mType.name()

    @property
    def mimeCategory(self):
        if self.mimeType:
            return self.mimeType.split('/')[0]

    @property
    def cid(self):
        return self.entry['Hash']

    @property
    def cidObject(self):
        return cidhelpers.getCID(self.cid)

    def getFullPath(self):
        """
        Returns the full IPFS path of the entry associated with this item
        (preserving file names) if we have the parent's hash, or the IPFS path
        with the entry's hash otherwise
        """

        if self._parentCid:
            return joinIpfs(os.path.join(cidConvertBase32(self._parentCid),
                                         self.filename))
        else:
            return joinIpfs(cidConvertBase32(self.entry['Hash']))

    def isRaw(self):
        return self.entry['Type'] == 0

    def isDir(self):
        return self.entry['Type'] == 1

    def isFile(self):
        return self.entry['Type'] == 2

    def isMetadata(self):
        return self.entry['Type'] == 3

    def isSymlink(self):
        return self.entry['Type'] == 4

    def isUnknown(self):
        return self.entry['Type'] == -1


class UnixFSDirectoryModel(QAbstractListModel):
    COL_UNIXFS_NAME = 0
    COL_UNIXFS_SIZE = 1
    COL_UNIXFS_MIME = 2
    COL_UNIXFS_HASH = 3

    def __init__(self, parent=None):
        super(UnixFSDirectoryModel, self).__init__(parent)

        self.app = QApplication.instance()
        self.entries = []

        self.iconFolder = getIcon('folder-open.png')
        self.iconFile = getIcon('file.png')
        self.iconUnknown = getIcon('unknown-file.png')

    def mimeData(self, indexes):
        mimedata = QMimeData()

        urls = []
        for idx in indexes:
            if not idx.isValid():
                continue

            eInfo = self.getEntryInfoFromIdx(idx)

            if eInfo:
                url = QUrl(eInfo.ipfsPath.ipfsUrl)
                urls.append(url)

        mimedata.setUrls(urls)
        return mimedata

    def canDropMimeData(self, data, action, row, column, parent):
        return True

    def clearModel(self):
        pass

    def getHashFromIdx(self, idx):
        eInfo = self.getEntryInfoFromIdx(idx)
        if eInfo:
            return eInfo.cid

    def rowCount(self, parent):
        return len(self.entries)

    def columnCount(self, parent):
        return 1

    def mimeFromDb(self, entry):
        mType = self.app.mimeDb.mimeTypeForFile(entry['Name'])
        if mType:
            return mType.name()

    def getEntryInfoFromIdx(self, idx):
        row = idx.row()

        if row in range(0, len(self.entries)):
            return self.entries[row]

    async def serializeEntries(self):
        pass

    def formatEntries(self):
        doc = []

        for eInfo in self.entries:
            doc.append(eInfo.entry)
        return doc

    def data(self, index, role):
        if not index.isValid():
            return QVariant(None)

        row = index.row()
        col = index.column()

        eInfo = self.getEntryInfoFromIdx(index)

        if role == Qt.DisplayRole:
            if row > len(self.entries):
                return QVariant(None)

            if col == self.COL_UNIXFS_NAME:
                return eInfo.filename
            elif col == self.COL_UNIXFS_SIZE:
                return 1
            elif col == self.COL_UNIXFS_MIME:
                return eInfo.mimeType
            elif col == self.COL_UNIXFS_HASH:
                return eInfo.cid
        elif role == Qt.DecorationRole:
            if eInfo.isDir():
                return self.iconFolder
            elif eInfo.isFile():
                if eInfo.mimeType:
                    mIcon = getMimeIcon(eInfo.mimeType)
                    if mIcon:
                        return mIcon
                    else:
                        return self.iconFile
                else:
                    return self.iconUnknown
        if role == Qt.ToolTipRole:
            return iUnixFSFileToolTip(eInfo)


class IPFSHashExplorerToolBox(GalacteekTab):
    """
    Organizes IPFSHashExplorerWidgets with a QToolBox
    """

    def __init__(self, gWindow, hashRef, maxItems=16, parent=None):
        super(IPFSHashExplorerToolBox, self).__init__(gWindow)

        self.rootHash = hashRef
        self.maxItems = maxItems

        self.toolbox = QToolBox()
        self.exLayout = QVBoxLayout()
        self.exLayout.addWidget(self.toolbox)

        self.vLayout.addLayout(self.exLayout)
        if self.rootHash:
            self.viewHash(self.rootHash)

    @property
    def itemsCount(self):
        return self.toolbox.count()

    def viewHash(self, hashRef, addClose=False, autoOpenFolders=False,
                 parentView=None):
        w = self.lookup(hashRef)
        if w:
            self.toolbox.setCurrentWidget(w)
            return True

        if self.itemsCount > self.maxItems:
            return False

        view = IPFSHashExplorerWidget(hashRef,
                                      parent=self, addClose=addClose,
                                      parentView=parentView,
                                      autoOpenFolders=autoOpenFolders)
        view.closeRequest.connect(functools.partial(
            self.remove, view))
        view.directoryOpenRequest.connect(
            lambda nView, cid: self.viewHash(cid, addClose=True,
                                             parentView=nView))

        idx = self.toolbox.addItem(view, getIconIpfsWhite(), hashRef)
        self.toolbox.setCurrentIndex(idx)
        view.reFocus()
        return True

    def lookup(self, hashRef):
        for idx in range(self.itemsCount):
            if self.toolbox.itemText(idx) == hashRef:
                return self.toolbox.widget(idx)

    def remove(self, view):
        idx = self.toolbox.indexOf(view)
        if idx:
            self.toolbox.removeItem(idx)
            # Always display previous index in the stack
            if self.itemsCount > 0:
                rIdx = idx - 1
                view = self.toolbox.widget(rIdx)
                if view:
                    self.toolbox.setCurrentWidget(view)
                    view.reFocus()


class IPFSHashExplorerStack(GalacteekTab):
    """
    Organizes IPFSHashExplorerWidgets with a QStackedWidget
    """

    def __init__(self, gWindow, hashRef, maxItems=16, parent=None):
        super(IPFSHashExplorerStack, self).__init__(gWindow)

        self.rootHash = hashRef
        self.maxItems = maxItems

        self.stack = QStackedWidget(self)
        self.exLayout = QVBoxLayout()
        self.exLayout.addWidget(self.stack)

        self.vLayout.addLayout(self.exLayout)
        if self.rootHash:
            self.viewHash(self.rootHash)

    @property
    def itemsCount(self):
        return self.stack.count()

    def viewHash(self, hashRef, addClose=False, autoOpenFolders=False,
                 parentView=None):
        view = IPFSHashExplorerWidget(hashRef,
                                      parent=self, addClose=addClose,
                                      showCidLabel=True,
                                      autoOpenFolders=autoOpenFolders,
                                      parentView=parentView)
        view.closeRequest.connect(partialEnsure(
            self.remove, view))
        view.directoryOpenRequest.connect(
            lambda nView, cid: self.viewHash(cid, addClose=True,
                                             parentView=nView))

        self.stack.insertWidget(self.stack.count(), view)
        self.stack.setCurrentWidget(view)
        view.reFocus()
        return True

    async def remove(self, view):
        try:
            view.cancelTasks()
            self.stack.removeWidget(view)
        except:
            pass

    async def onClose(self):
        for idx in range(self.stack.count()):
            widget = self.stack.widget(idx)
            await widget.cleanup()

        return True


class TreeEventFilter(QObject):
    copyPressed = pyqtSignal()
    returnPressed = pyqtSignal()
    backspacePressed = pyqtSignal()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress:
            modifiers = event.modifiers()

            key = event.key()
            if key == Qt.Key_Return:
                self.returnPressed.emit()
                return True
            if key == Qt.Key_Backspace:
                self.backspacePressed.emit()
                return True
            if modifiers & Qt.ControlModifier:
                if key == Qt.Key_C:
                    self.copyPressed.emit()
                    return True
        return False


class IPFSDirectoryListView(QListView):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setObjectName('unixFsDirView')
        self.setSpacing(5)
        self.setUniformItemSizes(True)
        self.viewModeList()
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setContextMenuPolicy(Qt.CustomContextMenu)

    def viewModeList(self):
        self.setViewMode(QListView.ListMode)
        self.setFlow(QListView.TopToBottom)
        self.setWrapping(False)

    def viewModeIcon(self):
        self.setViewMode(QListView.IconMode)
        self.setFlow(QListView.LeftToRight)
        self.setWrapping(True)


class IPFSHashExplorerWidget(QWidget):
    closeRequest = pyqtSignal()
    directoryOpenRequest = pyqtSignal(QObject, str)
    fileOpenRequest = pyqtSignal(IPFSPath)
    parentCidSet = pyqtSignal(str)

    def __init__(self, hashRef, addClose=False,
                 mimeDetectionMethod='db',
                 addActions=True, autoOpenFiles=True,
                 showCidLabel=False,
                 showGit=False,
                 hideHashes=False,
                 autoOpenFolders=False,
                 parentView=None,
                 parent=None):
        super(IPFSHashExplorerWidget, self).__init__(parent)

        self.parent = parent
        self.parentView = parentView
        self.setObjectName('unixFsExplorer')

        self.app = QApplication.instance()
        self.gWindow = self.app.mainWindow
        self.mimeDetectionMethod = mimeDetectionMethod

        self.loadingCube = AnimatedLabel(
            RotatingCubeRedFlash140d(speed=100),
            parent=self
        )
        self.loadingCube.clip.setScaledSize(QSize(24, 24))
        self.loadingCube.hide()

        self.model = UnixFSDirectoryModel(self)
        self.gitEnabled = showGit
        self.hideHashes = hideHashes

        self.listTask = None

        self.parentButton = None
        self.rootHash = None
        self.rootPath = None
        self.changeCid(hashRef)

        self.mainLayout = QVBoxLayout(self)
        self.setLayout(self.mainLayout)

        self.autoOpenFolders = autoOpenFolders
        self.autoOpenFiles = autoOpenFiles
        self.hLayoutTop = QHBoxLayout()
        self.hLayoutInfo = QHBoxLayout()
        self.hLayoutCtrl = QHBoxLayout()

        spacerItem = QSpacerItem(10, 20, QSizePolicy.Expanding,
                                 QSizePolicy.Minimum)

        self.hLayoutTop.addLayout(self.hLayoutCtrl)
        self.hLayoutTop.addItem(spacerItem)
        self.hLayoutTop.addLayout(self.hLayoutInfo)

        self.labelInfo = QLabel()

        self.hLayoutInfo.addWidget(self.loadingCube, 0, Qt.AlignLeft)
        self.hLayoutInfo.addWidget(self.labelInfo, 0, Qt.AlignLeft)

        if addClose:
            self.closeButton = QPushButton('Close')
            self.closeButton.clicked.connect(self.onCloseView)
            self.closeButton.setMaximumWidth(100)
            self.closeButton.setShortcut(QKeySequence('Backspace'))
            self.hLayoutCtrl.addWidget(self.closeButton, 0, Qt.AlignLeft)

        if addActions:
            self.addButtons()

        if showCidLabel:
            path = IPFSPath(self.rootHash, autoCidConv=True)
            labelCid = IPFSUrlLabel(path)
            clipButton = IPFSPathClipboardButton(path)
            hashmarkButton = HashmarkThisButton(path)
            pyrDropButton = self.app.mainWindow.getPyrDropButtonFor(path)

            layout = QHBoxLayout()
            layout.addWidget(labelCid)
            layout.addWidget(clipButton)
            layout.addWidget(hashmarkButton)
            layout.addWidget(pyrDropButton)
            layout.addItem(QSpacerItem(10, 20, QSizePolicy.Expanding,
                                       QSizePolicy.Minimum))
            self.mainLayout.addLayout(layout)

        self.mainLayout.addLayout(self.hLayoutTop)

        self.dirListView = IPFSDirectoryListView(self)
        self.dirListView.setModel(self.model)

        self.mainLayout.addWidget(self.dirListView)

        self.initModel()

        self.dirListView.customContextMenuRequested.connect(self.onContextMenu)
        self.dirListView.doubleClicked.connect(self.onDoubleClicked)

        self.evfilter = IPFSTreeKeyFilter(self.dirListView)
        self.evfilter.copyHashPressed.connect(self.onCopyItemHash)
        self.evfilter.copyPathPressed.connect(self.onCopyItemPath)
        self.evfilter.returnPressed.connect(self.onReturnPressed)
        self.evfilter.backspacePressed.connect(self.onBackspacePressed)

        self.dirListView.installEventFilter(self.evfilter)
        self.iconFolder = getIcon('folder-open.png')
        self.iconFile = getIcon('file.png')
        self.iconUnknown = getIcon('unknown-file.png')

        self.updateTree()

    @property
    def parentCid(self):
        if self.parentView:
            return self.parentView.rootHash

    def statusLoading(self, busy=True):
        if busy:
            self.loadingCube.setVisible(busy)
            self.loadingCube.startClip()
            self.loadingCube.clip.setSpeed(10)
        else:
            self.loadingCube.setVisible(False)
            self.loadingCube.stopClip()

    def changeCid(self, cid):
        if cidValid(cid):
            self.rootHash = cid
            self.rootPath = IPFSPath(self.rootHash, autoCidConv=True)
            self.cid = cidhelpers.getCID(self.rootHash)
            self.initModel()

    def goToParent(self):
        if self.parentCid:
            self.changeCid(self.parentCid)
            self.updateTree()

    def initModel(self):
        self.itemRoot = QModelIndex()

    def addButtons(self):
        self.getTask = None
        self.getButton = QPushButton(iDownload())
        self.getButton.clicked.connect(self.onGet)
        self.getLabel = QLabel()
        self.getProgress = QProgressBar()
        self.getProgress.setMinimum(0)
        self.getProgress.setMaximum(100)
        self.getProgress.hide()

        self.markButton = QPushButton(getIcon('hashmarks.png'), iHashmark())
        self.markButton.clicked.connect(self.onHashmark)

        self.pinButton = QPushButton(iPin())
        self.pinButton.clicked.connect(self.onPin)

        self.hLayoutCtrl.addWidget(self.getButton)
        self.hLayoutCtrl.addWidget(self.pinButton)
        self.hLayoutCtrl.addWidget(self.markButton)
        self.hLayoutCtrl.addWidget(self.getLabel)
        self.hLayoutCtrl.addWidget(self.getProgress)

    def setInfo(self, text):
        self.labelInfo.setText(text)

    def reFocus(self):
        self.dirListView.setFocus(Qt.OtherFocusReason)

    async def cleanup(self):
        self.cancelTasks()

    def cancelTasks(self):
        if self.listTask:
            self.listTask.cancel()

    @ipfsOp
    async def gitClone(self, ipfsop, entry, dest):
        """
        Clone the git repository contained within entry, to directory dest
        """
        from git.repo import base
        from git.exc import InvalidGitRepositoryError

        self.gitButton.setEnabled(False)

        mHash = cidConvertBase32(entry['Hash'])
        getRet = await ipfsop.client.get(mHash,
                                         dstdir=self.app.tempDir.path())
        if not getRet:
            self.gitButton.setEnabled(True)
            return messageBox('Could not fetch the git repository')

        repoPath = os.path.join(self.app.tempDir.path(), mHash)
        try:
            repo = base.Repo(repoPath)
        except InvalidGitRepositoryError:
            return messageBox(iGitInvalid())

        dstPath = '{}.git'.format(
            os.path.join(dest, self.rootHash))

        # Clone it now. No need to run it in a threadpool since the git module
        # will run a git subprocess for the cloning
        try:
            repo.clone(dstPath)
        except Exception as e:
            self.gitButton.setEnabled(True)
            return messageBox(iGitErrorCloning(str(e)))

        messageBox(iGitClonedRepo(dstPath))
        self.gitButton.setEnabled(True)

    def addGitControl(self, gitEntry):
        """
        Adds a tool button for making operations on the git repo
        """
        def clone(entry):
            dirSel = directorySelect()
            if dirSel:
                self.app.task(self.gitClone, entry, dirSel)

        self.gitMenu = QMenu(parent=self)
        self.gitMenu.addAction(iGitClone(), lambda: clone(gitEntry))
        self.gitButton = QToolButton(self)
        self.gitButton.setText('Git')
        self.gitButton.setMenu(self.gitMenu)
        self.gitButton.setPopupMode(QToolButton.MenuButtonPopup)
        self.hLayoutCtrl.addWidget(self.gitButton)

    def onHashmark(self):
        ensure(addHashmarkAsync(str(self.rootPath)))

    def onGet(self):
        dirSel = directorySelect()
        if dirSel:
            self.getTask = self.app.task(self.getResource,
                                         self.rootPath.objPath,
                                         dirSel)

    def onPin(self):
        self.app.task(self.app.ipfsCtx.pinner.queue,
                      self.rootPath.objPath, True,
                      None)

    def onReturnPressed(self):
        currentIdx = self.dirListView.currentIndex()
        if currentIdx.isValid():
            self.onDoubleClicked(currentIdx)

    def onCopyItemHash(self):
        entryInfo = self.model.getEntryInfoFromIdx(
            self.dirListView.currentIndex())
        if entryInfo:
            self.app.setClipboardText(entryInfo.cid)

    def onCopyItemPath(self):
        entryInfo = self.model.getEntryInfoFromIdx(
            self.dirListView.currentIndex())
        if entryInfo:
            self.app.setClipboardText(entryInfo.getFullPath())

    def onBackspacePressed(self):
        pass

    def onCloseView(self):
        self.closeRequest.emit()

    def browse(self, hash):
        self.gWindow.addBrowserTab().browseIpfsHash(hash)

    def browseFs(self, path):
        self.gWindow.addBrowserTab().browseFsPath(path)

    def onDoubleClicked(self, idx):
        entryInfo = self.model.getEntryInfoFromIdx(idx)

        if entryInfo.isDir():
            self.directoryOpenRequest.emit(self, entryInfo.cid)
        elif entryInfo.isFile() or entryInfo.isUnknown():
            self.openFile(entryInfo)

    def openFile(self, entryInfo):
        # if item.mimeType is None:
        #    return self.browse(fileHash)

        if self.autoOpenFiles:
            ensure(self.openWithRscOpener(entryInfo))
        else:
            self.fileOpenRequest.emit(entryInfo.ipfsPath)

    @ipfsOp
    async def openWithRscOpener(self, ipfsop, entry):
        # Pass a null mimetype to the resource opener so that it
        # redetects the mimetype with a full (but slower) mime detection method
        opener = self.app.resourceOpener
        await opener.open(entry.getFullPath(), None)

    def updateTree(self):
        if self.rootPath and self.rootPath.valid:
            self.listTask = self.app.task(
                self.listObject,
                self.rootPath.objPath,
                parentItem=self.itemRoot)

    def onContextMenu(self, point):
        selModel = self.dirListView.selectionModel()
        rows = selModel.selectedRows()

        if len(rows) == 0:
            return

        item = self.model.getEntryInfoFromIdx(rows.pop())
        menu = QMenu(self)

        def pinRecursive():
            self.app.task(
                self.app.ipfsCtx.pinner.queue, item.getFullPath(), True, None)

        def download():
            dirSel = directorySelect()
            self.app.task(self.getResource, item.getFullPath(),
                          dirSel)

        menu.addAction(getIcon('clipboard.png'),
                       iCopyCIDToClipboard(),
                       functools.partial(self.app.setClipboardText,
                                         item.cid)
                       )

        menu.addAction(getIcon('clipboard.png'),
                       iCopyPathToClipboard(),
                       functools.partial(self.app.setClipboardText,
                                         item.getFullPath()))

        menu.addAction(getIcon('clipboard.png'),
                       iCopyPubGwUrlToClipboard(),
                       functools.partial(
                           self.app.setClipboardText,
                           item.ipfsPath.publicGwUrl
        ))

        menu.addSeparator()
        menu.addAction(getIcon('hashmarks.png'),
                       iHashmark(),
                       partialEnsure(
            addHashmarkAsync,
            item.ipfsPath.objPath,
            item.filename
        )
        )
        menu.addSeparator()

        menu.addAction(getIcon('pin.png'), iPinRecursive(),
                       pinRecursive)
        menu.addAction(iDownload(), download)

        menu.exec(self.dirListView.mapToGlobal(point))

    async def timedList(self, ipfsop, objPath, parentItem, autoexpand,
                        secs, resolve_type):
        return await asyncio.wait_for(
            self.list(ipfsop, objPath,
                      parentItem=parentItem, autoexpand=autoexpand,
                      resolve_type=resolve_type), secs)

    @ipfsOp
    async def listObject(self, ipfsop, objPath, parentItem,
                         autoexpand=False, timeout=60 * 10):
        """ Lists contents of IPFS object referenced by objPath,
            and change the tree's model afterwards.

            We first try with resolve-type set to true, and if that gets a
            timeout do the same call but without resolving nodes types
        """

        self.statusLoading(True)

        try:
            self.setInfo(iLoading())
            await self.timedList(ipfsop, objPath, parentItem,
                                 autoexpand, timeout, True)
        except asyncio.TimeoutError:
            self.setInfo(iTimeoutTryNoResolve())

            try:
                await self.timedList(ipfsop, objPath, parentItem,
                                     autoexpand, timeout, False)
            except asyncio.TimeoutError:
                # That's a dead end .. bury that hash please ..
                self.setInfo(iTimeoutInvalidHash())
                return
        except aioipfs.APIError as err:
            self.setInfo(iIpfsError(err.message))
        except Exception as e:
            self.setInfo(iGeneralError(str(e)))
        else:
            rStat = await ipfsop.objStat(objPath)
            statInfo = StatInfo(rStat)

            if statInfo.valid and self.cid:
                self.setInfo(iCIDInfo(self.cid.version,
                                      statInfo.numLinks,
                                      sizeFormat(statInfo.totalSize)))

        self.statusLoading(False)

    async def list(self, ipfsop, path, parentItem=None,
                   autoexpand=False, resolve_type=True):
        """
        Does the actual directory listing with a streamed ls call
        """

        eCount = 0  # entry count
        wiStart, wiEnd = None, None
        startLt = self.app.loop.time()
        stCount, stLt = None, None

        _cp, dePath, deExists = self.app.multihashDb.pathDirEntries(
            self.rootPath.objPath)

        # Load from local cache or do a streamed ls
        if deExists:
            eGenerator = self.app.multihashDb.getDirEntries(
                self.rootPath.objPath)
        else:
            eGenerator = ipfsop.listStreamed(path, resolve_type)

        async for entry in eGenerator:
            eCount += 1

            rowCount = self.model.rowCount(QModelIndex())
            rowStart = max(0, rowCount - 1)
            rowEnd = rowStart + 1

            # Set start window index for the first time
            if wiStart is None:
                wiStart = self.model.index(rowStart, 0, QModelIndex())

            # Set end window index
            wiEnd = self.model.index(rowEnd, 0, QModelIndex())

            # Create the entry and add it to the model
            entryInfo = EntryInfo(entry, self.rootHash)
            entryInfo.mimeFromDb(self.app.mimeDb)
            self.model.entries.append(entryInfo)

            # Emit dataChanged() periodically
            if divmod(eCount, 64)[1] == 0:
                self.model.dataChanged.emit(wiStart, wiEnd)

                # The model was refreshed, update the window index
                wiStart = self.model.index(rowStart, 0, QModelIndex())

            if divmod(eCount, 4)[1] == 0:
                self.loadingCube.clip.setSpeed(
                    min(
                        self.loadingCube.clip.speed() + 5,
                        400
                    )
                )

                stCount, stLt = eCount, self.app.loop.time()

                entriesPerSec = int(stCount / (stLt - startLt))
                self.setInfo(iLoadedEntries(eCount, entriesPerSec))

            # Give it some deserved sleep
            await ipfsop.sleep(0.05)

        #
        # The CID was fully listed
        # Emit dataChanged() and serialize the entries
        #

        self.model.dataChanged.emit(wiStart, wiEnd)

        if not deExists:
            await self.serializeEntries()

    async def serializeEntries(self):
        await self.app.multihashDb.writeDirEntries(
            self.rootPath.objPath, self.model.formatEntries())

    @ipfsStatOp
    async def getResource(self, ipfsop, rPath, dest, rStat):
        """
        Get the resource referenced by rPath to directory dest
        """

        statInfo = StatInfo(rStat)
        if not statInfo.valid:
            return

        self.getProgress.show()

        async def onGetProgress(ref, bytesRead, arg):
            per = int((bytesRead / statInfo.totalSize) * 100)
            self.getLabel.setText('Downloaded: {0}'.format(
                sizeFormat(bytesRead)))
            self.getProgress.setValue(per)

        await ipfsop.client.get(rPath, dstdir=dest,
                                progress_callback=onGetProgress,
                                chunk_size=524288)

        self.getLabel.setText('Download finished')
        self.getProgress.hide()


class IPFSHashExplorerWidgetFollow(IPFSHashExplorerWidget):
    def __init__(self, hashRef, parent=None, **kwargs):
        super(IPFSHashExplorerWidgetFollow, self).__init__(
            hashRef, parent=parent, **kwargs)

        self.directoryOpenRequest.connect(self.onOpenDir)

    def onOpenDir(self, cid):
        self.changeCid(cid)
        self.updateTree()

    def onBackspacePressed(self):
        self.goToParent()
