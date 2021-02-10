from PyQt5.QtWidgets import QWidget
from PyQt5.QtWidgets import QTreeWidget
from PyQt5.QtWidgets import QTreeWidgetItem
from PyQt5.QtWidgets import QStackedWidget
from PyQt5.QtWidgets import QHeaderView
from PyQt5.QtWidgets import QCompleter

from PyQt5.QtCore import Qt
from PyQt5.QtCore import QUrl
from PyQt5.QtCore import QObject
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtCore import QModelIndex
from PyQt5.QtCore import QItemSelectionModel

from galacteek import log
from galacteek import database
from galacteek import partialEnsure
from galacteek import AsyncSignal
from galacteek.core.modelhelpers import *
from galacteek.core import runningApp
from galacteek.config import cGet

from galacteek.core.models.bm import BMContactsModel

from galacteek.database import bmMailBoxRegister
from galacteek.database import bmMailBoxList
from galacteek.database import bmMailBoxCount
from galacteek.database import bmMailBoxGetDefault
from galacteek.database import bmMailBoxGet
from galacteek.database.models.bm import BitMessageMailBox

from galacteek.dweb.markdown import markitdown

from galacteek.services.net.bitmessage.storage import BitMessageMailDir
from galacteek.services.net.bitmessage.storage import MaildirMessage
from galacteek.services.net.bitmessage import bmAddressExtract
from galacteek.services.net.bitmessage import bmAddressValid

from ..forms import ui_dmessenger
from ..forms import ui_dmessenger_compose
from ..forms import ui_dmessenger_messageview
from ..forms import ui_dmessenger_newmailboxdialog
from ..forms import ui_dmessenger_addcontactdialog

from ..dialogs import BaseDialog
from ..fonts import QFont
from ..helpers import *
from ..widgets import IconSelector
from ..clipboard import iCopiedToClipboard


class CreateMailBoxDialog(BaseDialog):
    uiClass = ui_dmessenger_newmailboxdialog.Ui_NewBitMessageMailBoxDialog

    def __init__(self, parent=None):
        super().__init__(parent=parent)

        self.iconSelector = IconSelector(self)
        self.ui.buttonBox.accepted.connect(self.accept)
        self.ui.buttonBox.rejected.connect(self.reject)
        self.ui.gridLayout.addWidget(self.iconSelector, 2, 1)

    def options(self):
        return {
            'label': self.ui.mailBoxLabel.text(),
            'type': self.ui.mailBoxType.currentText(),
            'iconCid': self.iconSelector.iconCid,
            'default': self.ui.useAsDefault.checkState() == Qt.Checked
        }


class AddContactDialog(BaseDialog):
    """
    Dialog to add a BM contact
    """

    uiClass = ui_dmessenger_addcontactdialog.Ui_AddContactDialog

    def __init__(self, bmAddress, parent=None):
        super().__init__(parent=parent)

        self.ui.buttonBox.accepted.connect(self.accept)
        self.ui.buttonBox.rejected.connect(self.reject)

        self.ui.newGroup.textEdited.connect(self.onNewGroupEdit)
        self.ui.bmAddress.setText(bmAddress)

    @property
    def address(self):
        return self.ui.bmAddress.text()

    @property
    def fullname(self):
        return self.ui.fullname.text()

    def onNewGroupEdit(self, text):
        self.ui.comboGroup.setEnabled(False)

    def accept(self):
        if self.fullname:
            self.done(0)
        else:
            messageBox('Fullname is empty')

    def options(self):
        ngroup = self.ui.newGroup.text()
        group = ngroup if ngroup else self.ui.comboGroup.currentText()

        return {
            'bmAddress': self.address,
            'fullname': self.fullname,
            'group': group
        }


async def addContact(bmAddress):
    dialog = AddContactDialog(bmAddress)
    await runDialogAsync(dialog)

    if dialog.result() == 0:
        options = dialog.options()

        await database.bmContactAdd(
            options['bmAddress'],
            options['fullname'],
            groupName=options['group']
        )


class MessageViewer(QWidget):
    curMessage: MaildirMessage = None

    msgReplyStart = pyqtSignal(MaildirMessage)
    msgComposeNewReq = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.app = runningApp()

        self.ui = ui_dmessenger_messageview.Ui_MessengerMailView()
        self.ui.setupUi(self)

        self.ui.replyButton.setEnabled(False)
        self.ui.replyButton.clicked.connect(self.onReply)

        self.browser.setOpenExternalLinks(False)
        self.browser.setOpenLinks(False)
        self.browser.anchorClicked.connect(
            partialEnsure(self.onOpenMessageLink))
        # self.browser.document().setDefaultStyleSheet()

    @property
    def browser(self):
        return self.ui.textBrowser

    @property
    def document(self):
        return self.browser.document()

    async def onOpenMessageLink(self, url):
        from galacteek.browser.schemes import isUrlSupported

        if isUrlSupported(url):
            tab = self.app.mainWindow.addBrowserTab()
            tab.enterUrl(url)
        elif url.scheme() == 'mailto':
            email = url.adjusted(QUrl.RemoveScheme).toString().lstrip(' ')
            bmAddr = bmAddressExtract(email)
            if not bmAddr:
                return

            contact = await database.bmContactByAddr(bmAddr)
            if not contact:
                await addContact(bmAddr)
            else:
                self.msgComposeNewReq.emit(bmAddr)

    def onReply(self):
        if self.curMessage:
            self.msgReplyStart.emit(self.curMessage)

    async def showMessage(self, message: MaildirMessage):
        self.document.clear()
        self.browser.clearHistory()

        try:
            mfrom = message['From']
            mto = message['To']
        except Exception:
            self.browser.insertPlainText('Invalid message')
            return

        senderContact, recpContact = None, None
        try:
            sender = bmAddressExtract(mfrom)
            recp = bmAddressExtract(mto)
            senderContact = await database.bmContactByAddr(sender)
            recpContact = await database.bmContactByAddr(recp)
        except Exception:
            # Invalid BM addrs ?
            self.browser.insertPlainText('Invalid message')
            return

        senderName = senderContact.fullname if senderContact else sender
        recpName = recpContact.fullname if recpContact else recp

        subject = message.get('Subject')
        date = message.get('Date')

        self.browser.insertHtml(
            f'<p>From: <a href="mailto: '
            f'{sender}@bitmessage">{senderName}</a></p>\n')
        self.browser.insertHtml('<br />')
        self.browser.insertHtml(
            f'<p>To: <a href="mailto: {recp}@bitmessage">{recpName}</a></p>\n')
        self.browser.insertHtml('<br />')

        if subject:
            self.browser.insertHtml(f"<p>Subject: {subject}</p><br />\n")

        if date:
            self.browser.insertHtml(f"<p>Date: {date}</p><br />\n")

        # self.browser.insertPlainText("\n")

        for part in message.walk():
            try:
                # contentType = message.get_content_type()
                # contentTypeParams = message.get_params()

                # Assume markdown
                markupType = message.get_param('markup', failobj='markdown')

                payload = part.get_payload()

                if markupType == 'markdown':
                    self.browser.insertHtml(markitdown(payload))
                    self.browser.insertHtml('<br />')
                else:
                    self.browser.insertPlainText(payload)
            except Exception:
                continue

        self.curMessage = message
        self.ui.replyButton.setEnabled(True)


class ComposeRecipientCompleter(QCompleter):
    def pathFromIndex(self, index):
        sibling = index.sibling(index.row(), 0)
        return super().pathFromIndex(sibling)


class MessageComposer(QWidget):
    cancelled = pyqtSignal()
    sendSuccess = pyqtSignal(str)

    def __init__(self, bmModel=None, parent=None):
        super().__init__(parent)

        self.app = runningApp()
        self.completer = ComposeRecipientCompleter(self)
        self.completer.setCompletionMode(QCompleter.PopupCompletion)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setCompletionColumn(1)
        self.completer.setModel(bmModel)

        self.ui = ui_dmessenger_compose.Ui_MessageComposerForm()
        self.ui.setupUi(self)

        self.ui.sendButton.clicked.connect(
            partialEnsure(self.onSend))
        self.ui.cancelButton.clicked.connect(self.onCancelClicked)

        self.ui.msgTo.setCompleter(self.completer)

    @property
    def messengerWidget(self):
        return self.parent()

    @property
    def subject(self):
        return self.ui.msgSubject.text()

    @property
    def recipient(self):
        return self.ui.msgTo.text()

    @subject.setter
    def subject(self, s: str):
        self.ui.msgSubject.setText(s)

    @recipient.setter
    def recipient(self, s: str):
        self.ui.msgTo.setText(s)

    @property
    def messageBody(self):
        return self.ui.msgText.toPlainText()

    def setSender(self, bmAddress: str):
        self.ui.msgFrom.clear()
        self.ui.msgFrom.addItem(bmAddress)
        # self.ui.msgFrom.setCurrentText(bmAddress)

    def setRecipient(self, bmAddress: str):
        self.recipient = bmAddress

    def startReply(self, message: MaildirMessage):
        fromKey = bmAddressExtract(message['From'])

        self.ui.msgTo.setText(fromKey if fromKey else message['From'])

        subject = message.get('Subject', '')
        if not subject.startswith('Re: '):
            self.ui.msgSubject.setText("Re: " + subject)
        else:
            self.ui.msgSubject.setText(subject)

        text = '\n\n'
        text += '-' * 80
        text += '\n'

        try:
            text += message.get_payload()
        except Exception:
            pass

        self.ui.msgText.setPlainText(text)
        self.ui.msgText.setFocus(Qt.OtherFocusReason)

    def composeNew(self):
        self.ui.msgTo.setText('')
        self.subject = ''
        self.recipient = ''
        self.ui.msgText.setPlainText('')
        self.ui.msgTo.setFocus(Qt.OtherFocusReason)

    def onCancelClicked(self, *qa):
        if self.messageBody and not areYouSure():
            return

        self.cancelled.emit()

    async def onSend(self, *args):
        if not bmAddressValid(self.recipient):
            return await messageBoxAsync('Invalid recipient BM address')

        # curMailDir = self.messengerWidget.bmCurrentMailDir
        curMailDir = None

        result = await self.app.s.bmService.mailer.send(
            self.ui.msgFrom.currentText(),
            self.ui.msgTo.text(),
            self.ui.msgSubject.text(),
            self.messageBody,
            mailDir=curMailDir
        )

        if result is True:
            self.sendSuccess.emit(self.ui.msgTo.text())
        else:
            await messageBoxAsync('Send error !')


class MessageHandlingError(Exception):
    pass


class MessageListView(QTreeWidget):
    messageNeedsDisplay = pyqtSignal()

    viewActive = pyqtSignal()

    def __init__(self, maildir: BitMessageMailDir, parent=None):
        super().__init__(parent)

        self.maildir = maildir
        self.currentItemChanged.connect(
            partialEnsure(self.onCurMessageChanged))
        self.setColumnCount(2)
        self.setHeaderLabels(['Subject', 'Date'])
        self.setHeaderHidden(True)
        self.setSortingEnabled(True)

        self.sMessageNeedsDisplay = AsyncSignal(MaildirMessage)

        self.viewActive.connect(self.onViewSwitched)

        self.fontNoBold = QFont(self.font())
        self.fontNoBold.setBold(False)
        self.fontBold = QFont(self.font())
        self.fontBold.setBold(True)

        self.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.hideColumn(1)

    @property
    def selModel(self):
        return self.selectionModel()

    def onViewSwitched(self):
        self.selectLatestMessage(force=True)

    async def onNewMessageReceived(self, key, msg):
        # TODO: UI notification here

        await self.insertMessage(key, msg)

    async def onCurMessageChanged(self, item, itemPrevious, *a):
        mKey = item.data(0, Qt.UserRole)
        if not mKey:
            return

        msg = await self.maildir.getMessageByKey(mKey)
        await self.sMessageNeedsDisplay.emit(msg)

        for col in range(0, self.columnCount()):
            item.setFont(col, self.fontNoBold)

        # Mark it as read
        msg.set_subdir('cur')
        self.maildir.updateMessage(mKey, msg)

    async def insertMessage(self, mKey, msg):
        idxL = self.model().match(
            self.model().index(0, 0, QModelIndex()),
            Qt.UserRole,
            mKey,
            -1,
            Qt.MatchFixedString | Qt.MatchWrap | Qt.MatchRecursive
        )

        if len(idxL) > 0:
            raise MessageHandlingError(f'Already exists: {mKey}')

        msgSubDir = msg.get_subdir()

        itemFrom = QTreeWidgetItem(self)
        itemFrom.setText(0, msg['Subject'])
        itemFrom.setData(0, Qt.UserRole, mKey)
        itemFrom.setToolTip(0, mKey)
        itemFrom.setText(1, msg['Date'])
        itemFrom.setText(2, msg['From'])

        if msgSubDir == 'new':
            for col in range(0, 3):
                itemFrom.setFont(col, self.fontBold)
        elif msgSubDir == 'cur':
            pass

        self.insertTopLevelItem(0, itemFrom)
        self.sortByColumn(1, Qt.DescendingOrder)

    async def refresh(self):
        async for mKey, msg in self.maildir.yieldNewMessages():
            try:
                await self.insertMessage(mKey, msg)
            except Exception as err:
                log.debug(f'Refresh error: {err}')
                continue

        self.selectLatestMessage(force=True)

    def selectLatestMessage(self, force=False):
        # Select latest message
        curIndex = self.selModel.currentIndex()
        if not curIndex.isValid() or force:
            idxLatest = self.model().index(0, 0, QModelIndex())
            if idxLatest.isValid():
                self.selModel.select(
                    idxLatest, QItemSelectionModel.Select)
                self.selModel.setCurrentIndex(
                    idxLatest, QItemSelectionModel.SelectCurrent)

    def keyPressEvent(self, event):
        key = event.key()

        if key == Qt.Key_Delete:
            # Message removal

            curIndex = self.selModel.currentIndex()
            if not curIndex:
                return

            messageId = self.model().data(curIndex, Qt.UserRole)
            if messageId and areYouSure():
                if self.maildir.msgRemoveInbox(messageId):
                    self.model().removeRow(curIndex.row())

        super().keyPressEvent(event)


class Messenger(QObject):
    bmCurrentMailDir: BitMessageMailDir = None
    messageBoxViews: []
    mBoxStack: QStackedWidget = None

    def __init__(self, mBoxStack, parent=None):
        super().__init__(parent)

        self.mBoxStack = mBoxStack


class MessengerWidget(QWidget):
    bmCurrentMailDir: BitMessageMailDir = None
    bmCurrentMailBox: BitMessageMailBox = None
    currentView: MessageListView = None
    messageBoxViews: dict = {}

    def __init__(self, parent=None):
        super().__init__(parent)

        self.app = runningApp()
        self.ui = ui_dmessenger.Ui_MessengerForm()
        self.ui.setupUi(self)

        self.modelContacts = BMContactsModel(self)

        self.messenger = Messenger(self.ui.mailBoxStack, self)

        self.messageView = MessageViewer(self)
        self.messageView.msgReplyStart.connect(
            self.onReplyToMessage
        )
        self.messageView.msgComposeNewReq.connect(
            self.composeMessage
        )

        self.messageComposer = MessageComposer(bmModel=self.modelContacts,
                                               parent=self)
        self.messageComposer.cancelled.connect(
            self.setDefaultView)
        self.messageComposer.sendSuccess.connect(
            self.onSendSuccess)

        self.sIdxView = self.ui.mailStack.addWidget(self.messageView)
        self.sIdxCompose = self.ui.mailStack.addWidget(self.messageComposer)

        self.ui.createMailboxButton.clicked.connect(
            partialEnsure(self.onCreateMailbox))
        self.ui.copyBmKeyButton.clicked.connect(
            partialEnsure(self.onCopyBmKey))
        self.ui.composeButton.clicked.connect(
            self.onComposeClicked)
        self.ui.curMailboxCombo.currentIndexChanged.connect(
            partialEnsure(self.onMailBoxSelect))

        self.setDefaultView()

    @property
    def bmComboText(self):
        return self.ui.curMailboxCombo.currentText()

    @property
    def bmComboItems(self):
        return [
            self.ui.curMailboxCombo.itemText(idx) for idx in
            range(self.ui.curMailboxCombo.count())
        ]

    @property
    def isComposing(self):
        return self.ui.mailStack.currentIndex() == self.sIdxCompose

    def resizeEventNo(self, event):
        try:
            for _addr, view in self.messageBoxViews.items():
                view.setMaximumWidth(self.width() / 3)
        except Exception:
            pass

    def setDefaultView(self):
        self.ui.mailStack.setCurrentIndex(self.sIdxView)

    def setComposeView(self):
        self.ui.mailStack.setCurrentIndex(self.sIdxCompose)

    async def updateMailBoxList(self):
        defaultIcon = getIcon('dmessenger/dmessenger.png')

        for bmBox in await bmMailBoxList():
            log.debug(f'updateMailBoxList: {bmBox.bmAddress}')

            if bmBox.bmAddress not in self.bmComboItems:
                if bmBox.iconCid:
                    # icon = await getIconFromIpfs(
                    #     ipfsop, IPFSPath(bmBox.iconCid))
                    icon = None

                    self.ui.curMailboxCombo.addItem(
                        icon if icon else defaultIcon,
                        bmBox.bmAddress
                    )
                else:
                    self.ui.curMailboxCombo.addItem(
                        defaultIcon,
                        bmBox.bmAddress
                    )

    async def setup(self):
        if await bmMailBoxCount() == 0:
            bmMb, bmKey, mailDir = await self.createMailBox(
                label='me', select=False,
                default=True
            )

        await self.updateMailBoxList()

        bmMbDefault = await bmMailBoxGetDefault()

        if bmMbDefault:
            self.bmCurrentMailBox = bmMbDefault

            await self.selectMailBox(bmMbDefault.bmAddress)

    def composeMessage(self, bmRecipient: str, subject: str = None):
        ensure(self.composePrepare(bmRecipient, subject=subject))

    async def onMessageDisplay(self, message):
        await self.messageView.showMessage(message)

        if not self.isComposing and 0:
            self.setDefaultView()

    async def onMailBoxSelect(self, idx, *qa):
        await self.selectMailBox(self.bmComboText)

    def onReplyToMessage(self, message):
        self.messageComposer.setSender(
            self.bmCurrentMailBox.bmAddress
        )
        self.messageComposer.startReply(message)
        self.setComposeView()

    def onSendSuccess(self, recipient):
        self.setDefaultView()

    def onComposeClicked(self):
        ensure(self.composePrepare())

    async def composePrepare(self, bmRecipient: str = None,
                             subject: str = None):
        if self.bmCurrentMailBox:
            await self.modelContacts.update()

            self.messageComposer.composeNew()
            self.messageComposer.setSender(
                self.bmCurrentMailBox.bmAddress
            )

            if bmRecipient:
                self.messageComposer.recipient = bmRecipient

            if subject:
                self.messageComposer.subject = subject

            self.setComposeView()

    async def switchMailDir(self, bmAddr, maildir: BitMessageMailDir):
        view = self.messageBoxViews.get(bmAddr)

        if not view:
            view = MessageListView(maildir, parent=self.ui.mailBoxStack)

            self.messageBoxViews[bmAddr] = view
            idx = self.ui.mailBoxStack.addWidget(view)
            self.ui.mailBoxStack.setCurrentIndex(idx)

            view.sMessageNeedsDisplay.connectTo(
                self.onMessageDisplay)
            maildir.sNewMessage.connectTo(
                view.onNewMessageReceived)
        else:
            self.ui.mailBoxStack.setCurrentWidget(view)

        self.currentView = view

        view.viewActive.emit()

        await view.refresh()

    async def onCopyBmKey(self, *args):
        if self.bmComboText:
            self.app.setClipboardText(self.bmComboText)
            await messageBoxAsync(iCopiedToClipboard())

    async def onCreateMailbox(self, *args):
        dlg = CreateMailBoxDialog()
        await runDialogAsync(dlg)

        opts = dlg.options()

        await self.createMailBox(
            label=opts['label'],
            select=True,
            iconCid=opts['iconCid'],
            default=opts['default']
        )

        await self.updateMailBoxList()

    async def selectMailBox(self, bmAddress: str):
        # self.ui.curMailboxCombo.setCurrentText(bmAddress)
        mailBox = await bmMailBoxGet(bmAddress)

        if mailBox:
            self.ui.curMailboxCombo.setCurrentText(bmAddress)

            key, mailDir = await self.app.s.bmService.mailer.getMailBox(
                bmAddress)
            self.bmCurrentMailDir = mailDir
            self.bmCurrentMailBox = mailBox

            await self.switchMailDir(bmAddress, mailDir)

    async def createMailBox(self, label='default', select=False,
                            default=False,
                            iconCid=None):
        defaultIconCid = cGet('bmAccount.defaultIconCid')

        key, mailDir = await self.app.s.bmService.mailer.createMailBox()

        log.debug(f'Create BM: {key}, {mailDir}')

        if not key:
            return None, None, None

        await mailDir.storeWelcome()

        if select:
            await self.selectMailBox(key)

        bmMailBox = await bmMailBoxRegister(
            key,
            label,
            key,
            default=default,
            iconCid=iconCid if iconCid else defaultIconCid
        )

        if bmMailBox:
            return key, bmMailBox, mailDir
