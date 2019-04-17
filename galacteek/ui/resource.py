import os
import os.path
import asyncio
import aioipfs

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QObject

from galacteek.dweb.page import PDFViewerPage
from galacteek.dweb.page import WebTab, DWebView
from galacteek.ipfs import ipfsOp
from galacteek.ipfs.mimetype import detectMimeType
from galacteek.ipfs.mimetype import isDirectoryMtype
from galacteek.ipfs.cidhelpers import isIpfsPath
from galacteek.ipfs.cidhelpers import shortPathRepr
from galacteek import log
from galacteek import logUser

from . import ipfsview
from .helpers import getMimeIcon
from .imgview import ImageViewerTab


class IPFSResourceOpener(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.app = QApplication.instance()
        self.setObjectName('resourceOpener')

    @ipfsOp
    async def open(self, ipfsop, rscPath, mimeType):
        """
        Open the resource referenced by rscPath according
        to its MIME type

        :param str rscPath: IPFS CID/path
        :param str mimeType: MIME type
        """

        if self.app.mainWindow.pinAllGlobalChecked:
            await ipfsop.ctx.pinner.queue(rscPath, False,
                                          None,
                                          qname='default')

        rscShortName = shortPathRepr(rscPath)

        if isIpfsPath(rscPath):
            # Try to reuse metadata from the multihash store
            rscMeta = await self.app.multihashDb.get(rscPath)
            if rscMeta:
                mimeType = rscMeta.get('mimetype')

        if mimeType is None:
            mimeType = await detectMimeType(rscPath)

        if mimeType:
            mimeCategory = mimeType.split('/')[0]
        else:
            mimeCategory = None

        if mimeCategory == 'text' and mimeType != 'text/html':
            tab = ipfsview.TextViewerTab()
            tab.show(rscPath)
            return self.app.mainWindow.registerTab(
                tab,
                rscShortName,
                icon=getMimeIcon('text/x-generic'),
                tooltip=rscPath,
                current=True
            )

        if mimeCategory == 'image':
            tab = ImageViewerTab(rscPath, self.app.mainWindow)
            return self.app.mainWindow.registerTab(
                tab,
                rscShortName,
                icon=getMimeIcon('image/x-generic'),
                tooltip=rscPath,
                current=True
            )

        if mimeCategory == 'video' or mimeCategory == 'audio':
            tab = self.app.mainWindow.addMediaPlayerTab()
            if tab:
                tab.playFromPath(rscPath)
            return

        if mimeType == 'application/pdf' and 0:  # not usable yet
            tab = WebTab(self.app.mainWindow)
            tab.attach(
                DWebView(page=PDFViewerPage(rscPath))
            )
            return self.app.mainWindow.registerTab(
                tab,
                rscShortName,
                icon=getMimeIcon('application/pdf'),
                tooltip=rscPath,
                current=True
            )

        if isDirectoryMtype(mimeType):
            return self.app.mainWindow.addBrowserTab().browseFsPath(rscPath)

        if mimeType == 'text/html':
            return self.app.mainWindow.addBrowserTab().browseFsPath(rscPath)

        # Use system's default application

        logUser.debug('Opening {0} with preferred application'.format(
            rscPath))
        return await self.openWithSystemDefault(rscPath)

    @ipfsOp
    async def openWithExternal(self, ipfsop, rscPath, progArgs):
        filePath = os.path.join(self.app.tempDir.path(),
                                os.path.basename(rscPath))

        if not os.path.exists(filePath):
            try:
                await ipfsop.client.get(rscPath,
                                        dstdir=self.app.tempDir.path())
            except aioipfs.APIError as err:
                log.debug(err.message)
                return

        if not os.path.isfile(filePath):
            # Bummer
            return

        for idx, value in enumerate(progArgs):
            if value == '%f':
                progArgs[idx] = filePath

        try:
            proc = await asyncio.create_subprocess_exec(
                *progArgs,
                stdout=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
        except BaseException as err:
            os.unlink(filePath)
            log.debug(str(err))
        else:
            os.unlink(filePath)

    @ipfsOp
    async def openWithSystemDefault(self, ipfsop, rscPath):
        # Use xdg-open or open depending on the platform
        if self.app.system == 'Linux':
            await self.openWithExternal(rscPath, ['xdg-open', '%f'])
        elif self.app.system == 'Darwin':
            await self.openWithExternal(rscPath, ['open', '%f'])
