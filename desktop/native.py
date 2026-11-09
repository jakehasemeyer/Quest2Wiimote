"""The Windows desktop application. Native Qt widgets and an OpenGL preview."""
import concurrent.futures
import json
import os
import sys
import time

from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QGridLayout, QLabel, QPushButton, QComboBox, QDoubleSpinBox, QCheckBox, QGroupBox,
    QFormLayout, QDialog, QDialogButtonBox, QPlainTextEdit, QMessageBox, QProgressBar, QFrame, QScrollArea, QSlider)

import server
from paths import ROOT, WORK
from preview import TrackingCameraView,TrackingView
from profiles import configure_dolphin
from screen import VirtualScreen, relative_ray
from headset_view import HeadsetView

STYLE='''
QWidget { background:#101620; color:#dfe7f1; font-family:"Segoe UI"; font-size:12px; }
QMainWindow,QDialog { background:#101620; }
QGroupBox { background:#151e2a; border:1px solid #2a3849; border-radius:8px; margin-top:14px; padding:17px 10px 10px; }
QGroupBox::title { subcontrol-origin:margin; left:12px; padding:0 5px; color:#c6d1df; font-weight:600; }
QLabel { background:transparent; }
QLabel#title { font-size:26px; font-weight:600; }
QLabel#muted { color:#8f9db0; }
QLabel#brand { font-size:18px; font-weight:600; color:#76e5c2; }
QPushButton { background:#202d3e; border:1px solid #37475b; border-radius:5px; padding:9px 10px; }
QPushButton:hover { background:#2b3e53; border-color:#5a7897; }
QPushButton:disabled { color:#637286; background:#192330; }
QPushButton#primary { background:#69dfb7; color:#102b23; border-color:#69dfb7; font-weight:600; }
QComboBox,QDoubleSpinBox { background:#111a26; border:1px solid #37485c; border-radius:4px; padding:6px; min-height:18px; }
QComboBox::drop-down { border:0; width:18px; }
QComboBox QAbstractItemView { background:#192738; selection-background-color:#34516c; }
QCheckBox { spacing:7px; padding:5px 0; }
QCheckBox::indicator { width:14px; height:14px; border:1px solid #60738a; border-radius:3px; }
QCheckBox::indicator:checked { background:#6be5be; border-color:#6be5be; }
QProgressBar { background:#101925; border:0; border-radius:3px; max-height:6px; }
QProgressBar::chunk { background:#65dcb6; border-radius:3px; }
QStatusBar { color:#91a3b9; background:#0d141d; }
QPlainTextEdit { background:#0b131d; border:1px solid #34465b; font-family:Consolas; }
'''


def text(value, muted=False):
    label=QLabel(value)
    if muted:label.setObjectName('muted')
    label.setWordWrap(True)
    return label


def button(label,callback,primary=False):
    b=QPushButton(label);b.clicked.connect(callback)
    if primary:b.setObjectName('primary')
    return b


class JoystickView(QWidget):
    def __init__(self, color):
        super().__init__();self.setFixedSize(112,112);self.color=QColor(color)
        self.raw=[0.,0.];self.output=[0.,0.];self.deadzone=.12;self.active=False
        self.setToolTip('Hollow dot: raw stick position. Colored dot: output to Dolphin. Shaded circle: deadzone. Up is positive Y.')

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        cx,cy,r=56,46,38
        p.setPen(QPen(QColor('#60738a'),1));p.setBrush(QColor('#101620'));p.drawEllipse(QRectF(cx-r,cy-r,2*r,2*r))
        p.setPen(QPen(QColor('#37475b'),1));p.drawLine(cx-r,cy,cx+r,cy);p.drawLine(cx,cy-r,cx,cy+r)
        d=r*self.deadzone;p.setPen(QPen(QColor('#a58a58'),1));p.setBrush(QColor(209,169,108,35));p.drawEllipse(QRectF(cx-d,cy-d,2*d,2*d))
        x,y=self.raw;p.setBrush(Qt.NoBrush);p.setPen(QPen(QColor('#aab8c8'),1.5));p.drawEllipse(QRectF(cx+x*r-6,cy-y*r-6,12,12))
        x,y=self.output;p.setPen(Qt.NoPen);p.setBrush(self.color if self.active else QColor('#60738a'));p.drawEllipse(QRectF(cx+x*r-3.5,cy-y*r-3.5,7,7))
        p.setPen(QColor('#aab8c8'));p.drawText(QRectF(0,89,112,20),Qt.AlignCenter,f'X {x:+.2f}  Y {y:+.2f}');p.end()


class DisplayGuides(QWidget):
    def __init__(self):
        super().__init__(None,Qt.Tool|Qt.FramelessWindowHint|Qt.WindowStaysOnTopHint|Qt.WindowTransparentForInput|Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setStyleSheet('background:transparent;')
        self.mode='16:9'

    def paintEvent(self,event):
        p=QPainter(self);p.setPen(QPen(QColor('#77e8c1'),1))
        ratio=4/3 if self.mode=='4:3' else 16/9
        w=min(self.width(),self.height()*ratio);h=w/ratio
        x=(self.width()-w)/2;y=(self.height()-h)/2;cx=self.width()/2;cy=self.height()/2
        p.drawLine(int(x),int(cy),int(x+w),int(cy));p.drawLine(int(cx),int(y),int(cx),int(y+h));p.end()


class Calibration(QDialog):
    def __init__(self,station,mode):
        super().__init__(station)
        self.station=station;self.mode=mode;self.points=[];self.previous=True
        self.setWindowTitle('Virtual screen calibration');self.resize(680,420)
        layout=QVBoxLayout(self);self.title=text('');self.title.setFont(QFont('Segoe UI',23));layout.addWidget(self.title)
        self.instruction=text('');layout.addWidget(self.instruction)
        self.status=text('',True);layout.addWidget(self.status);layout.addStretch()
        self.capture=button('Capture with right trigger or click here',self.capture_point,True);layout.addWidget(self.capture)
        layout.addWidget(button('Cancel · Esc',self.reject))
        self.poller=QTimer(self);self.poller.timeout.connect(self.poll);self.poller.start(15)
        server.STATE.calibrating=True
        server.STATE.aligning_dolphin=mode=='dolphin'
        self.next_step()
        if mode in ('center','dolphin'):
            self.resize(620,280)
            self.setWindowFlags(Qt.Tool|Qt.WindowStaysOnTopHint)
            station.show_display_guides(True)

    def next_step(self):
        if self.mode=='center':
            self.title.setText('Aim at the overlay crosshair over Dolphin')
            self.instruction.setText('Switch focus to Dolphin. Keep the headset stationary, aim the right controller at the transparent overlay’s center, hold steady, then press/release its trigger once. The entered physical screen size and distance define the virtual plane. Move this small guide aside if needed.')
        elif self.mode=='dolphin':
            edge=['left','right','top','bottom'][len(self.points)]
            self.title.setText('Dolphin alignment · '+edge+' midpoint')
            location={'left':'halfway down the left edge','right':'halfway down the right edge','top':'halfway across the top edge','bottom':'halfway across the bottom edge'}[edge]
            self.instruction.setText('Switch focus to Dolphin. Move the GAME cursor '+location+' of the game picture, excluding window borders and black bars. At first contact with the edge, stop and press/release the right trigger. You can aim beyond the virtual screen during alignment. Game buttons are temporarily blocked. Move this guide if it obscures the cursor.')
            self.previous=True
        else:
            name=['top-left','top-right','bottom-left'][len(self.points)]
            self.title.setText('Capture the '+name+' corner')
            self.instruction.setText('Hold the same point of the right controller next to the physical display’s '+name+' corner, then press its trigger. Keep the controller orientation consistent and the headset fixed. All corners must be within tracking coverage. Use center-aim calibration if the display cannot be reached.')
            self.previous=True

    def poll(self):
        frame=server.STATE.snapshot()
        try:relative_ray(frame);ready=True
        except ValueError:ready=False
        if self.mode=='dolphin':ready=ready and frame['pointer']['valid']
        self.capture.setEnabled(ready)
        self.status.setText(('Right controller ready · '+frame['pointer']['reason']) if ready and self.mode=='dolphin' else 'Right controller: 6DoF tracking ready' if ready else 'Waiting for a tracked right-controller aim ray. Keep it in front of the Quest cameras and close Quest system menus.')
        pressed=frame['hands'][0].get('trigger',0)>.65
        if pressed and not self.previous and ready:self.capture_point()
        self.previous=pressed

    def capture_point(self):
        try:
            frame=server.STATE.snapshot()
            with server.STATE.lock:
                if self.mode=='center':server.STATE.screen.calibrate_center(frame);self.accept();return
                if self.mode=='dolphin':
                    pointer=frame['pointer']
                    if not pointer['valid']:raise ValueError(pointer['reason'])
                    axis='dolphin_u' if len(self.points)<2 else 'dolphin_v'
                    self.points.append(max(0.,min(1.,pointer[axis])))
                    if len(self.points)==4:
                        server.STATE.screen.align_dolphin(*self.points);self.accept();return
                    self.next_step();return
                point,_=relative_ray(frame);self.points.append(point)
                if len(self.points)==3:
                    server.STATE.screen.calibrate_corners(*self.points);self.accept();return
            self.next_step()
        except ValueError as error:
            QMessageBox.warning(self,'Calibration',str(error))
            if len(self.points)==(4 if self.mode=='dolphin' else 3):self.points=[];self.next_step()

    def done(self,result):
        self.poller.stop()
        server.STATE.calibrating=False
        server.STATE.aligning_dolphin=False
        if self.mode in ('center','dolphin'):self.station.show_display_guides(self.station.guides.isChecked())
        super().done(result)


class Station(QMainWindow):
    def __init__(self,services):
        super().__init__();self.services=services;self.setWindowTitle('Quest2Wiimote · Tracking station');self.resize(1460,820);self.setMinimumSize(1180,700)
        self.display_guides=DisplayGuides()
        self.pool=concurrent.futures.ThreadPoolExecutor(max_workers=1);self.jobs=[];self.command_buttons=[]
        server.STATE.screen=VirtualScreen(ROOT/'data/screen.json')
        icon=QPixmap(64,64);icon.fill(QColor('#142d27'));p=QPainter(icon);p.setRenderHint(QPainter.Antialiasing);p.setPen(Qt.NoPen);p.setBrush(QColor('#6be5be'));p.drawRoundedRect(10,19,44,26,7,7);p.setBrush(QColor('#142d27'));p.drawEllipse(18,27,9,9);p.drawEllipse(37,27,9,9);p.end();self.setWindowIcon(QIcon(icon))
        root=QWidget();self.setCentralWidget(root);outer=QHBoxLayout(root);outer.setContentsMargins(18,14,18,12);outer.setSpacing(18)
        sidebar=QWidget();sidebar.setFixedWidth(214);side=QVBoxLayout(sidebar);side.setContentsMargins(0,0,0,0)
        brand=text('Quest2Wiimote');brand.setObjectName('brand');side.addWidget(brand);side.addWidget(text('Windows tracking station',True));side.addSpacing(18)
        self.device_status=text('Looking for Quest…');side.addWidget(self.device_status)
        self.devices=QComboBox();self.devices.setAccessibleName('ADB device');side.addWidget(self.devices)
        self.add_command(side,'Refresh devices','refresh')
        side.addSpacing(10);self.add_command(side,'Install & start Quest app','install',True);self.add_command(side,'Start Quest app · USB','usb')
        self.passthrough=QCheckBox('Enable passthrough');self.passthrough.setChecked(server.STATE.passthrough_requested);side.addWidget(self.passthrough)
        self.passthrough.toggled.connect(self.set_passthrough);self.command_buttons.append(self.passthrough)
        self.passthrough_status=text('Checking headset passthrough…',True);side.addWidget(self.passthrough_status)
        self.headset_view=HeadsetView();side.addWidget(self.headset_view)
        self.tracking_camera=TrackingCameraView(server.STATE.snapshot);self.tracking_camera.hide();side.addWidget(self.tracking_camera)
        self.add_command(side,'Stop Quest app','stop')
        side.addSpacing(10);self.add_command(side,'Connect wireless ADB','wifi');self.add_command(side,'Disable wireless ADB','wifi-off')
        side.addWidget(text('Wireless ADB pairs over USB first. Use a trusted local network.',True))
        side.addStretch();self.add_command(side,'Open configured Dolphin','dolphin',True)
        side.addWidget(button('Apply Dolphin pointer profile',self.apply_profile));self.add_command(side,'Open Dolphin profiles','profiles');self.add_command(side,'Quest diagnostic log','logs')
        side.addWidget(text('Bridge files and build tools stay on E:.',True))
        left_scroll=QScrollArea();left_scroll.setFixedWidth(234);left_scroll.setWidgetResizable(True);left_scroll.setFrameShape(QFrame.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);left_scroll.setWidget(sidebar);outer.addWidget(left_scroll)
        content=QWidget();column=QVBoxLayout(content);column.setContentsMargins(0,0,0,0);column.setSpacing(12)
        title=text('Controller tracking');title.setObjectName('title');column.addWidget(title)
        column.addWidget(text('A stationary Quest. Touch controllers or hand tracking. A calibrated display for Dolphin.',True))
        stats=QHBoxLayout();self.live_label=text('Quest offline');self.rate_label=text('Tracking — Hz');self.fps_label=text('Preview — fps');self.dsu_label=text('DSU listening')
        for label in [self.live_label,self.rate_label,self.fps_label,self.dsu_label]:label.setFont(QFont('Segoe UI',12));stats.addWidget(label)
        column.addLayout(stats)
        tools=QHBoxLayout();frusta=QCheckBox('Camera frusta · estimated');frusta.setChecked(True);tools.addWidget(frusta)
        self.rate=QComboBox();self.rate.addItems(['72 fps preview','60 fps preview']);self.rate.setAccessibleName('Preview target frame rate');tools.addWidget(self.rate)
        self.wii_models=QCheckBox('Use Wiimote/Nunchuk Models');tools.addWidget(self.wii_models)
        self.hand_tracking=QCheckBox('Hand Tracking');self.hand_tracking.setChecked(server.STATE.hand_tracking_requested);tools.addWidget(self.hand_tracking);tools.addStretch()
        self.hand_tracking.setToolTip('Right thumb + index/middle/ring/pinky: A/B/1/2. Right thumbs up: +. Left thumb + index/middle: C/Z. Close either fist, then move 4 cm up/down/left/right for the D-pad; open to reset. Enable hand tracking in Quest settings and put the controllers down.')
        self.hand_tracking.toggled.connect(self.set_hand_tracking)
        tools.addWidget(button('Reset orbit',lambda:self.preview.reset_view()));column.addLayout(tools)
        model_tools=QHBoxLayout();self.swap_models=QCheckBox('Swap: Right Nunchuk / Left Wiimote');model_tools.addWidget(self.swap_models);model_tools.addStretch();column.addLayout(model_tools)
        hold_info=text('Automatic tracking-loss hold: 20 s · Nunchuk alone: 15 s',True)
        hold_info.setToolTip('Touch controllers keep their last tracked pose when tracking drops. Movement resumes as soon as tracking returns. The controller status shows Holding last position during the hold. Requires a live, focused Quest stream; does not apply to hand tracking.')
        column.addWidget(hold_info)
        self.hand_tracking_status=text('',True);column.addWidget(self.hand_tracking_status);self.hand_tracking_status.setVisible(self.hand_tracking.isChecked())
        base_controls=QHBoxLayout();self.base_lock=QCheckBox('Lock base position');self.base_lock.setChecked(server.STATE.screen.data['base_locked']);base_controls.addWidget(self.base_lock)
        self.base_status=text('Unlock to reposition the physical Quest and its model. Screen and orbit stay fixed.',True);base_controls.addWidget(self.base_status,1);column.addLayout(base_controls)
        self.base_lock.toggled.connect(self.lock_base)
        self.preview=TrackingView(server.STATE.snapshot,view_path=ROOT/'data/view.json');frusta.toggled.connect(lambda value:setattr(self.preview,'show_frusta',value));self.rate.currentIndexChanged.connect(lambda index:setattr(self.preview,'target_fps',72 if index==0 else 60))
        self.wii_models.setChecked(self.preview.use_wii_models);self.swap_models.setChecked(self.preview.swap_wii_models);self.swap_models.setEnabled(self.preview.use_wii_models)
        self.tracking_camera.use_wii_models=self.preview.use_wii_models;self.tracking_camera.swap_wii_models=self.preview.swap_wii_models
        self.wii_models.toggled.connect(self.set_wii_models);self.swap_models.toggled.connect(self.set_swap_models);column.addWidget(self.preview,1)
        cards=QHBoxLayout();self.hand_labels=[];self.hand_groups=[];self.joysticks=[];self.deadzone_sliders=[];self.deadzone_labels=[]
        for title,color in [('Right Touch · Wii Remote','#6be5be'),('Left Touch · Nunchuk','#82acff')]:
            group=QGroupBox(title);self.hand_groups.append(group);box=QVBoxLayout(group);row=QHBoxLayout();details=QVBoxLayout();row.addLayout(details,1);box.addLayout(row)
            status=text('Offline');status.setStyleSheet('color:'+color);details.addWidget(status)
            motion=text('Acceleration —\nGyroscope —',True);motion.setFont(QFont('Consolas',10));details.addWidget(motion)
            trigger=QProgressBar();trigger.setRange(0,100);trigger.setTextVisible(False);details.addWidget(trigger)
            buttons_label=text('Buttons released',True);details.addWidget(buttons_label);cards.addWidget(group);self.hand_labels.append((status,motion,trigger,buttons_label))
            slot=len(self.hand_labels)-1
            joystick=JoystickView(color);joystick.setAccessibleName(('Right' if slot==0 else 'Left')+' joystick');row.addWidget(joystick);self.joysticks.append(joystick)
            deadzone=QHBoxLayout();label=text('');self.deadzone_labels.append(label);deadzone.addWidget(label)
            slider=QSlider(Qt.Horizontal);slider.setRange(0,50);slider.setValue(round(server.STATE.stick_deadzones[slot]*100));slider.setAccessibleName(('Right' if slot==0 else 'Left')+' stick deadzone')
            slider.setToolTip('Ignore stick movement inside this radius. Applies immediately and is saved. 0% disables drift filtering; 50% ignores half the travel.')
            label.setText(f'Deadzone {slider.value()}%');deadzone.addWidget(slider,1);box.addLayout(deadzone);self.deadzone_sliders.append(slider)
            slider.valueChanged.connect(lambda value,slot=slot:self.set_stick_deadzone(slot,value))
            box.addWidget(button('Test '+('right' if slot==0 else 'left')+' rumble',lambda checked=False,slot=slot:self.test_rumble(slot)))
        self.update_model_labels();column.addLayout(cards);outer.addWidget(content,1)
        calibration=QWidget();calibration.setFixedWidth(260);right=QVBoxLayout(calibration);right.setContentsMargins(0,0,0,0)
        group=QGroupBox('Virtual screen & sensor bar');settings=QVBoxLayout(group)
        self.ir_enabled=QCheckBox('Use calibrated IR pointing');self.ir_enabled.setChecked(server.STATE.screen.data['enabled']);settings.addWidget(self.ir_enabled)
        settings.addWidget(text('The right controller aims at this screen plane. Dolphin receives absolute pointer coordinates.',True))
        form=QFormLayout();self.width=self.spin(.08,10,.01,' m');self.height=self.spin(.08,10,.01,' m');self.depth=self.spin(.08,10,.05,' m')
        data=server.STATE.screen.data;self.width.setValue(data['width']);self.height.setValue(data['height']);self.depth.setValue(data['distance'])
        self.display_mode=QComboBox();self.display_mode.addItems(['4:3','16:9']);self.display_mode.setCurrentText(data['display_mode']);self.display_mode.setAccessibleName('Wii display mode')
        self.display_mode.setToolTip('Pointer mapping changes immediately. Reopen Dolphin from this app to apply the same Wii and graphics aspect ratio.')
        form.addRow('Wii display mode',self.display_mode)
        self.ray_mode=QComboBox();self.ray_mode.addItem('Face buttons','face');self.ray_mode.addItem('Handle → top','handle')
        self.ray_mode.setCurrentIndex(self.ray_mode.findData(data['ray_mode']));self.ray_mode.setAccessibleName('Controller pointing end')
        self.ray_mode.setToolTip('Face buttons keeps the current aim. Handle → top starts at the handle bottom and points through the top of the controller. Applies immediately; the calibrated screen stays fixed.')
        form.addRow('Point from',self.ray_mode)
        form.addRow('Screen width',self.width);form.addRow('Screen height',self.height);form.addRow('Aim distance',self.depth)
        self.bar=QComboBox();self.bar.addItems(['Above screen','Below screen']);self.bar.setCurrentIndex(0 if data['bar']=='above' else 1);form.addRow('Sensor bar',self.bar);settings.addLayout(form)
        settings.addWidget(button('Calibrate by aiming at center',lambda:self.calibrate('center'),True))
        settings.addWidget(button('Capture three physical corners',lambda:self.calibrate('corners')))
        settings.addWidget(button('Save screen dimensions',self.save_settings))
        self.calibration_status=text('');settings.addWidget(self.calibration_status);self.pointer_status=text('',True);settings.addWidget(self.pointer_status)
        settings.addWidget(button('Align Dolphin cursor edges',lambda:self.calibrate('dolphin')))
        self.guides=QCheckBox('Crosshair overlay over Dolphin');self.guides.toggled.connect(self.show_display_guides);settings.addWidget(self.guides)
        settings.addWidget(button('Reset Dolphin alignment',self.reset_dolphin))
        self.alignment_status=text('',True);settings.addWidget(self.alignment_status)
        right.addWidget(group);right.addWidget(text('Center calibration: enter the physical display size and distance, then aim at its center and press the right trigger.\n\nCorner calibration: capture controller positions next to three reachable corners.\n\nUnlock the base before repositioning the Quest. The screen and orbit remain fixed in the workspace.',True))
        right.addStretch();right.addWidget(text('Preview target: 72 fps\nMotion source: Quest runtime rate\nMonitor refresh limits visible frames.',True))
        right.addWidget(text('Camera frusta are illustrative. Live position tracking requires controller visibility.',True))
        scroll=QScrollArea();scroll.setFixedWidth(280);scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.NoFrame);scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);scroll.setWidget(calibration);outer.addWidget(scroll)
        self.ir_enabled.toggled.connect(self.save_settings);self.bar.currentIndexChanged.connect(self.save_settings)
        self.display_mode.currentTextChanged.connect(self.set_display_mode)
        self.ray_mode.currentIndexChanged.connect(self.set_ray_mode)
        self.ticker=QTimer(self);self.ticker.timeout.connect(self.refresh);self.ticker.start(200);self.refresh()
        self.stick_ticker=QTimer(self);self.stick_ticker.timeout.connect(self.refresh_sticks);self.stick_ticker.start(33);self.refresh_sticks()
        self.statusBar().showMessage('DSU output: 127.0.0.1:26760 · Native OpenGL preview · Ready')

    @staticmethod
    def spin(minimum,maximum,step,suffix):
        widget=QDoubleSpinBox();widget.setRange(minimum,maximum);widget.setDecimals(2);widget.setSingleStep(step);widget.setSuffix(suffix);return widget

    def add_command(self,layout,label,action,primary=False):
        b=button(label,lambda:self.run_command(action),primary);layout.addWidget(b);self.command_buttons.append(b)

    def run_command(self,action):
        if self.jobs:return
        data=dict(serial=self.devices.currentData() or server.STATE.device,passthrough=self.passthrough.isChecked())
        def work():
            if action=='dolphin':configure_dolphin(self.ir_enabled.isChecked())
            return server.command(action,data)
        for b in self.command_buttons:b.setEnabled(False)
        self.statusBar().showMessage('Working: '+action+'…');self.jobs.append((action,self.pool.submit(work)))

    def save_settings(self,*args):
        try:
            with server.STATE.lock:server.STATE.screen.settings(self.width.value(),self.height.value(),self.depth.value(),self.ir_enabled.isChecked(),'above' if self.bar.currentIndex()==0 else 'below')
            self.statusBar().showMessage('Virtual screen settings saved. Apply the pointer profile and restart Dolphin after changing pointer mode.',7000)
        except ValueError as error:QMessageBox.warning(self,'Virtual screen',str(error))

    def calibrate(self,mode):
        if mode!='dolphin':self.save_settings()
        try:relative_ray(server.STATE.snapshot())
        except ValueError as error:QMessageBox.information(self,'Tracking required',str(error));return
        dialog=Calibration(self,mode)
        if dialog.exec()==QDialog.Accepted:
            data=server.STATE.screen.data;self.width.setValue(data['width']);self.height.setValue(data['height'])
            self.statusBar().showMessage('Dolphin cursor alignment saved and applied live.' if mode=='dolphin' else 'Screen calibrated. The right controller now aims at the saved display plane.',10000)
        dialog.deleteLater()

    def apply_profile(self):
        configure_dolphin(self.ir_enabled.isChecked());self.statusBar().showMessage('Dolphin profile applied. Restart Dolphin to load it. Previous controller configuration is backed up on E:.',10000)

    def reset_dolphin(self):
        with server.STATE.lock:server.STATE.screen.reset_dolphin()
        self.statusBar().showMessage('Dolphin alignment reset. Physical screen calibration is unchanged.',7000)

    def show_display_guides(self,visible):
        self.display_guides.mode=server.STATE.screen.data['display_mode']
        self.display_guides.setGeometry(self.screen().geometry())
        self.display_guides.setVisible(visible)
        self.display_guides.update()

    def set_display_mode(self,mode):
        with server.STATE.lock:server.STATE.screen.set_display_mode(mode)
        self.show_display_guides(self.display_guides.isVisible())
        self.statusBar().showMessage(mode+' pointer mapping applied. Reopen Dolphin from this app to apply the matching Wii display mode.',12000)

    def set_ray_mode(self,index):
        with server.STATE.lock:server.STATE.screen.set_ray_mode(self.ray_mode.itemData(index))
        self.statusBar().showMessage(self.ray_mode.itemText(index)+' pointing applied to Dolphin and both previews.',7000)

    def test_rumble(self,slot):
        try:self.statusBar().showMessage(server.command('rumble',{'slot':slot})['message'],5000)
        except (RuntimeError,ValueError) as error:QMessageBox.information(self,'Controller rumble',str(error))

    def set_passthrough(self,enabled):
        try:self.statusBar().showMessage(server.command('passthrough',{'enabled':enabled})['message'],5000)
        except (OSError,ValueError) as error:QMessageBox.warning(self,'Passthrough',str(error))

    def set_wii_models(self,enabled):
        self.preview.use_wii_models=self.tracking_camera.use_wii_models=enabled;self.swap_models.setEnabled(enabled);self.update_model_labels();self.preview.save_view();self.preview.update();self.tracking_camera.update()

    def set_stick_deadzone(self,slot,percent):
        try:server.STATE.set_stick_deadzone(slot,percent)
        except (OSError,ValueError) as error:
            slider=self.deadzone_sliders[slot];slider.blockSignals(True);slider.setValue(round(server.STATE.stick_deadzones[slot]*100));slider.blockSignals(False)
            QMessageBox.warning(self,'Stick deadzone',str(error))
        self.deadzone_labels[slot].setText(f'Deadzone {self.deadzone_sliders[slot].value()}%');self.refresh_sticks()

    def refresh_sticks(self):
        state=server.STATE.snapshot()
        for slot,view in enumerate(self.joysticks):
            hand=state['hands'][slot];view.raw=hand['raw_stick'];view.output=hand['stick'];view.deadzone=state['stick_deadzones'][slot];view.active=hand['active'];view.update()

    def set_hand_tracking(self,enabled):
        try:server.STATE.set_hand_tracking(enabled)
        except (OSError,ValueError) as error:
            self.hand_tracking.blockSignals(True);self.hand_tracking.setChecked(server.STATE.hand_tracking_requested);self.hand_tracking.blockSignals(False)
            QMessageBox.warning(self,'Hand Tracking',str(error))
        self.update_model_labels();self.refresh()

    def set_swap_models(self,enabled):
        self.preview.swap_wii_models=self.tracking_camera.swap_wii_models=enabled;self.update_model_labels();self.preview.save_view();self.preview.update();self.tracking_camera.update()

    def update_model_labels(self):
        roles=('Nunchuk','MotionPlus Remote') if self.preview.use_wii_models and self.preview.swap_wii_models else ('MotionPlus Remote','Nunchuk') if self.preview.use_wii_models else ('Wii Remote','Nunchuk')
        source='hand' if self.hand_tracking.isChecked() else 'Touch'
        for index,group in enumerate(self.hand_groups):group.setTitle(('Right ' if index==0 else 'Left ')+source+' · '+roles[index])

    def lock_base(self,locked):
        try:
            with server.STATE.lock:server.STATE.screen.lock_base(locked,server.STATE.snapshot())
            self.statusBar().showMessage('Base locked at its current position.' if locked else 'Base unlocked. Move the Quest; screen and orbit stay fixed.',8000)
        except ValueError as error:
            self.base_lock.blockSignals(True);self.base_lock.setChecked(server.STATE.screen.data['base_locked']);self.base_lock.blockSignals(False)
            QMessageBox.information(self,'Base tracking required',str(error))

    def refresh(self):
        state=server.STATE.snapshot();available=[(d['serial'],d['status']) for d in state['devices']]
        current=[(self.devices.itemData(i),self.devices.itemText(i).split(' · ')[-1]) for i in range(self.devices.count())]
        if available!=current:
            previous=self.devices.currentData();self.devices.clear()
            for serial,status in available:self.devices.addItem(serial+' · '+status,serial)
            found=self.devices.findData(previous or state['device'])
            if found>=0:self.devices.setCurrentIndex(found)
        battery=state.get('battery')
        self.device_status.setText('Quest 2 connected · '+(f'{battery}%' if battery is not None else 'Battery —') if state['device'] else 'Connect and authorize the Quest')
        self.live_label.setText('Quest streaming' if state['live'] else 'Session not focused' if state['hz'] else 'Quest offline')
        self.live_label.setStyleSheet('color:'+('#6be5be' if state['live'] else '#d1a96c'))
        self.rate_label.setText(f"Tracking {state['hz']:.1f} Hz")
        self.hand_tracking_status.setVisible(self.hand_tracking.isChecked())
        if self.hand_tracking.isChecked():
            message='Right: pinch A/B/1/2 · thumbs up +. Left: pinch C/Z. Either fist: move for D-pad.'
            if not state['hz'] or not state.get('hand_tracking_control'):message='Install & start the updated Quest app to use hand tracking.'
            elif not state.get('hand_tracking_supported'):message='Hand tracking unavailable. Enable hand tracking in Quest settings, then restart the Quest app.'
            elif not state.get('hand_tracking'):message='Switching to hand tracking…'
            elif not any(h['active'] for h in state['hands']):message='Put the controllers down and hold your hands in front of the Quest cameras.'
            self.hand_tracking_status.setText(message)
        if state.get('passthrough'):
            self.passthrough_status.setText('Passthrough active in Quest')
        elif not state.get('hz'):
            self.passthrough_status.setText('Start the Quest app to apply passthrough')
        elif not state.get('passthrough_control'):
            self.passthrough_status.setText('Install updated Quest app for live passthrough control')
        elif self.passthrough.isChecked() and not state.get('passthrough_supported'):
            self.passthrough_status.setText('Quest runtime reports passthrough unavailable')
        elif self.passthrough.isChecked() and state.get('passthrough_result',0)<0:
            self.passthrough_status.setText('Passthrough error '+str(state['passthrough_result']))
        else:self.passthrough_status.setText('Enabling passthrough…' if self.passthrough.isChecked() else 'Passthrough off')
        selected=self.devices.currentData() or state['device']
        serial=selected if any(d['serial']==selected and d['status']=='device' for d in state['devices']) else ''
        self.headset_view.sync(state['passthrough_desired'],serial)
        self.tracking_camera.setVisible(state['passthrough_desired'])
        fps=self.preview.fps();self.fps_label.setText(f'Preview {fps:.1f} fps');self.fps_label.setStyleSheet('color:'+('#6be5be' if fps>=59 else '#d1a96c'))
        subscribers=len(server.DSU_SERVER.subscriptions);self.dsu_label.setText(f'DSU · {subscribers} subscriptions' if subscribers else 'DSU listening')
        d=state['screen'];self.calibration_status.setText(('Calibrated · '+d['method']) if d['calibrated'] else 'Not calibrated')
        bounds=d['dolphin_bounds'];self.alignment_status.setText(f"Dolphin output span: {(bounds[2]-bounds[0])*100:.1f}% × {(bounds[3]-bounds[1])*100:.1f}%"+(' · aligned' if d['dolphin_aligned'] else ' · not aligned'))
        pointer=state['pointer'];self.pointer_status.setText(pointer['reason']+(f"\nX {pointer['u']:.3f} · Y {pointer['v']:.3f}\nDistance {pointer['screen_distance']:.2f} m · Roll {pointer['roll']*180:+.0f}°" if pointer['valid'] else ''))
        for index,(status,motion,trigger,buttons) in enumerate(self.hand_labels):
            hand=state['hands'][index];m=state['motion'][index];active=state['live'] and hand['active']
            status.setText('Holding last position' if active and hand.get('held') else 'Idle · press a left button to reconnect' if index==1 and state['left_idle'] else '6DoF tracked' if active and m['tracked'] else 'Rotation only' if active and m.get('orientation') else 'Offline / not tracked')
            fmt=lambda values:'  '.join(f'{v:+.2f}' for v in values)
            motion.setText('Accel  '+fmt(m['accel'])+' g\nGyro   '+fmt(m['gyro'])+' °/s' if active else 'Acceleration —\nGyroscope —')
            trigger.setValue(round(hand.get('trigger',0)*100) if active else 0)
            buttons.setText(hand['gesture'] if active and state.get('hand_tracking') and 'gesture' in hand else 'Buttons '+str(hand.get('buttons',0))+' · Grip '+f"{hand.get('squeeze',0):.2f}" if active else 'Buttons released')
        for job in self.jobs[:]:
            action,future=job
            if not future.done():continue
            self.jobs.remove(job)
            for b in self.command_buttons:b.setEnabled(True)
            try:
                result=future.result();message=result.get('message','Done')
                if action=='logs':
                    dialog=QDialog(self);dialog.setWindowTitle('Quest diagnostic log');dialog.resize(900,600);layout=QVBoxLayout(dialog);editor=QPlainTextEdit(message);editor.setReadOnly(True);layout.addWidget(editor);dialog.exec()
                else:self.statusBar().showMessage(message,12000)
            except Exception as error:QMessageBox.warning(self,'Quest2Wiimote',str(error));self.statusBar().showMessage(str(error),15000)

    def closeEvent(self,event):
        self.display_guides.close()
        self.headset_view.shutdown()
        with server.STATE.lock:server.STATE.screen.save()
        self.preview.save_view()
        self.ticker.stop();self.stick_ticker.stop();self.preview.timer.stop();self.pool.shutdown(wait=False,cancel_futures=True)
        self.preview.release_models();self.tracking_camera.timer.stop();self.tracking_camera.release_models()
        server.stop_services(self.services);event.accept()


def main():
    os.environ['TEMP']=os.environ['TMP']=str(WORK)
    app=QApplication(sys.argv);app.setApplicationName('Quest2Wiimote');app.setOrganizationName('Quest2Wiimote');app.setStyle('Fusion');app.setStyleSheet(STYLE)
    endpoint='Quest2Wiimote.NativeStation'
    client=QLocalSocket();client.connectToServer(endpoint)
    if client.waitForConnected(200):client.write(b'show');client.flush();client.waitForBytesWritten(200);return
    QLocalServer.removeServer(endpoint);listener=QLocalServer();listener.listen(endpoint)
    try:services=server.start_services()
    except OSError as error:QMessageBox.critical(None,'Quest2Wiimote','Close the previous desktop server, then try again.\n'+str(error));return
    window=Station(services)
    def activate():
        socket=listener.nextPendingConnection()
        if socket:socket.close();socket.deleteLater()
        window.showNormal();window.raise_();window.activateWindow()
    listener.newConnection.connect(activate);window.show()
    app.exec();listener.close()


if __name__=='__main__':main()
