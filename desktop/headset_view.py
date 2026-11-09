"""Embed our read-only scrcpy window inside the native tracking station."""
import ctypes
from ctypes import wintypes
import os
import subprocess
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QWindow
from PySide6.QtWidgets import QFrame,QLabel,QVBoxLayout,QWidget

from paths import ROOT, SCRCPY_DIR, WORK

user32=ctypes.WinDLL('user32',use_last_error=True)
user32.FindWindowW.argtypes=[wintypes.LPCWSTR,wintypes.LPCWSTR];user32.FindWindowW.restype=wintypes.HWND
user32.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
user32.PostMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]


class HeadsetView(QFrame):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setFixedHeight(120);self.setFrameShape(QFrame.StyledPanel)
        self.setAccessibleName('Live headset camera preview')
        self.setStyleSheet('QFrame { background:#080d13; border:1px solid #34465b; border-radius:4px; }')
        self.box=QVBoxLayout(self);self.box.setContentsMargins(1,1,1,1)
        self.message=QLabel('Connect the Quest to show its camera view.');self.message.setWordWrap(True);self.message.setAlignment(Qt.AlignCenter)
        self.box.addWidget(self.message)
        self.process=None;self.container=None;self.window=None;self.hwnd=None
        self.serial='';self.retry_at=0.;self.stopping_at=0.;self.started_at=0.
        self.title='Quest2Wiimote embedded camera '+str(os.getpid())+' '+str(id(self))
        self.hide()

    def sync(self,enabled,serial):
        self.setVisible(enabled)
        desired=serial if enabled else ''
        if self.process and self.process.poll() is not None:
            self.process=None;self.hwnd=None;self.window=None;self.serial='';self.stopping_at=0.
            if self.container:self.container.deleteLater();self.container=None
            self.message.show();self.message.setText('Camera view disconnected. Reconnecting…')
            self.retry_at=time.monotonic()+3
        if self.process and (desired!=self.serial or self.stopping_at):
            self.stop()
            if self.stopping_at and time.monotonic()-self.stopping_at>1.5 and self.process.poll() is None:self.process.terminate()
            return
        if not desired:
            self.message.setText('Connect the Quest to show its camera view.')
            return
        if not self.process and time.monotonic()>=self.retry_at:
            exe=next((SCRCPY_DIR).glob('*/scrcpy.exe'),None)
            if not exe:
                self.message.setText('Camera view is not installed.');self.retry_at=time.monotonic()+3
                return
            self.serial=desired;self.started_at=time.monotonic()
            self.message.setText('Opening camera view…')
            try:
                with (WORK/'embedded-camera.log').open('a',encoding='utf-8') as log:
                    self.process=subprocess.Popen([str(exe),'--serial',desired,'--window-title',self.title,
                        '--window-borderless','--window-x=-20000','--window-y=-20000',
                        '--window-width=212','--window-height=118','--no-window-aspect-ratio-lock',
                        '--max-size=640','--max-fps=72','--video-bit-rate=2M','--no-audio','--no-control',
                        '--no-clipboard-autosync','--no-terminal-title'],cwd=exe.parent,
                        stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
            except OSError:
                self.message.setText('Camera view unavailable. Retrying…');self.retry_at=time.monotonic()+3
                return
        if self.process and not self.container:
            hwnd=user32.FindWindowW(None,self.title);pid=wintypes.DWORD()
            if hwnd:user32.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
            if hwnd and pid.value==self.process.pid:
                self.hwnd=hwnd;self.window=QWindow.fromWinId(hwnd)
                if not self.window:self.stop();return
                self.container=QWidget.createWindowContainer(self.window,self)
                self.container.setFocusPolicy(Qt.NoFocus);self.container.setAttribute(Qt.WA_TransparentForMouseEvents)
                self.box.addWidget(self.container);self.message.hide();self.container.show()
            elif time.monotonic()-self.started_at>12:
                self.stop()

    def stop(self):
        if not self.process or self.stopping_at:return
        self.stopping_at=time.monotonic()
        if self.hwnd:user32.PostMessageW(self.hwnd,0x0010,0,0)  # WM_CLOSE, only our embedded viewer.
        else:self.process.terminate()

    def shutdown(self):
        self.stop()
        if self.process:
            try:self.process.wait(timeout=1.5)
            except subprocess.TimeoutExpired:self.process.terminate();self.process.wait(timeout=1.5)
        if self.container:self.container.deleteLater();self.container=None
        self.process=None;self.window=None;self.hwnd=None
