"""IFA bot for booking exams on https://portail.if-algerie.com"""

import ctypes
user32 = ctypes.WinDLL('user32')
try:
    import os
    import traceback
    import sys
    import json
    import re
    import shutil
    import random
    from time import sleep
    from datetime import datetime, date, time
    from pprint import pformat
    from functools import wraps
    from operator import attrgetter
    from typing import List, Any

    from PyQt5 import QtWidgets, uic
    from PyQt5.QtCore import QDate, QTime, QThread, QObject, pyqtSignal
    from PyQt5.QtGui import QIcon
    from webdriver_manager.chrome import ChromeDriverManager

    from modules.utils import kill_task, html2pdf
    from modules.exceptions import CloudFlareException, PaymentDayException,\
        NoToastException, ElementClickInterceptedException,\
        StaleElementReferenceException, CaptchaCrashException,\
        BadCredentialsException, UnableToLoginException,\
        ImpossibleToReserveException, NetworkException,\
        BadScoreException, PlusTardException, NoSuchElementException,\
        PageCrashException, NoExamsThisDayException, is_critical_exception
    from modules.widgets import UserEditDialog, ProxyEditDialog, RangeEdit, RangeEditFloat
    from modules.api import IFAUser, Exam
except Exception as e:
    user32.MessageBoxW(0, str(e), e.__class__.__name__, 0x0)
    raise e

DIR = os.path.dirname(__file__)
UI_PATH = DIR+'\\files\\main.ui'
CONFIG_PATH = os.environ['AppData']+'\\IFA_bot\\config.json'
ENCODING = 'u8'
PROXY_PATTERN = r'^([^h]|h([^t]|t([^t]|t([^p]|p([^s:]|s([^:]|:([^\/]|\/([^\/])))|:([^\/]|\/([^\/])))))))\S+:\d+$'
NOT_FNAME_CHAR_PATTERN = r"[^\w ,.\-@%#№+=`'~^\[\]\(\)]"
USER_AGENTS = [ua.replace('en-US', 'fr-FR')
               for ua in open(
                DIR + '\\files\\chrome_useragents.txt').read().splitlines()
               if 'en-US' in ua
              ]
DELAY_AWAIT_EXAM = 30


def try_handle(func):
    """try or print traceback in App console"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            if args and hasattr(args[0], 'log'):
                args[0].log(traceback.format_exc())
            else:
                print(traceback.format_exc())
            return None

    return wrapper


def auto_save(func):
    """automatic saving of app settings after func execution"""

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        res = func(self, *args, **kwargs)
        self.save_settings()
        return res

    return wrapper


class Worker(QThread, QObject):
    "Single Thread worker for booking exam as one user"
    errored = pyqtSignal(dict, str, Exception)
    crashed = pyqtSignal(dict, str, Exception, str)
    booked = pyqtSignal(dict, str, Exam)
    finished = pyqtSignal(object)

    def __init__(self, user: dict, *args, anticaptcha_key='',
                 delay_range_await_exam=[20,30],
                 delay_range_unable_to_login=[0,0],
                 delay_range_cant_reserve=[0,0],
                 delay_range_network_exception=[0,0],
                 delay_range_bad_score=[0,0],
                 delay_range_plus_tard=[0,0],
                 delay_range_page_crash=[0,0],
                 delay_range_recharger=[0,0],
                 delay_range_chrome_restart=[0,0],
                 max_attempts=3,
                 **kwargs):
        super().__init__()
        self.user = user
        self.args = args
        self.kwargs = kwargs
        self.anticaptcha_key = anticaptcha_key
        self.delay_range_await_exam=delay_range_await_exam
        self.delay_range_unable_to_login=delay_range_unable_to_login
        self.delay_range_cant_reserve=delay_range_cant_reserve
        self.delay_range_network_exception=delay_range_network_exception
        self.delay_range_bad_score=delay_range_bad_score
        self.delay_range_plus_tard=delay_range_plus_tard
        self.delay_range_page_crash=delay_range_page_crash
        self.delay_range_recharger=delay_range_recharger
        self.delay_range_chrome_restart=delay_range_chrome_restart
        self.max_attempts=max_attempts

    def run(self):
        cloudflare_attempts = 0
        refreshes = 0
        user = self.user
        proxys = user['proxys'].copy()
        proxy = (proxys.pop(0) if proxys else '')
        exams = None
        exam = None
        self.driver = None
        try:
            while True:
                try:
                    if proxy is None:
                        cloudflare_attempts = 0
                        if not proxys:
                            break
                        proxy = proxys.pop(0)
                        if getattr(self, 'driver', None) is not None:
                            self.driver.proxy = proxy
                            self.driver.restart()
                    if getattr(self, 'driver', None) is None:
                        isolation_path = os.environ['USERPROFILE'] + '\\AppData\\Local\\Google\\Chrome\\User Data\\Isolated\\' + re.sub(NOT_FNAME_CHAR_PATTERN, '_', user['username'])
                        os.makedirs(isolation_path, exist_ok=True)
                        if not os.path.isdir(isolation_path + '\\' + user['profile']):
                            shutil.copytree(os.environ['USERPROFILE'] + '\\AppData\\Local\\Google\\Chrome\\User Data\\' + user['profile'], isolation_path + '\\' + user['profile'])
                        self.driver = IFAUser(
                            user['username'], user['password'],
                            'chromedriver.exe',
                            *self.args,
                            user_data_dir=isolation_path,
                            profile=user['profile'],
                            anticaptcha_key=self.anticaptcha_key,
                            proxy=proxy, **self.kwargs)
                    if exam is None:
                        if exams is None:
                            exams = self.driver.query(
                                        date_from=date(*user['date_from']),
                                        date_to=date(*user['date_to']),
                                        time_from=time(*user['time_from']),
                                        time_to=time(*user['time_to']),
                                        type=('' if user['type'] == 'All'
                                              else user['type'])).__iter__()
                        exam = next(exams)

                    exam.book(f'results\\{exam.dt:%Y-%m-%d_%H_%M}_{exam.type}_'
                              + re.sub(NOT_FNAME_CHAR_PATTERN, 
                                       "_", user['username'])
                              + '.html')
                    self.booked.emit(user, proxy, exam)
                    break
                except StopIteration:
                    # no exams for user
                    exam = None
                    exams = None
                    refreshes = 0
                    sleep(random.uniform(*self.delay_range_await_exam))
                except (PaymentDayException,
                        NoToastException,
                        ElementClickInterceptedException,
                        StaleElementReferenceException):
                    print(repr(e))
                # except (BadCredentialsException, UnableToLoginException) as e:
                except BadCredentialsException as e:
                    self.errored.emit(user, proxy, e)
                    break
                except UnableToLoginException as e:
                    self.errored.emit(user, proxy, e)
                    sleep(random.uniform(*self.delay_range_unable_to_login))
                except ImpossibleToReserveException as e:
                    # ..35 days before reserving other exam
                    self.errored.emit(user, proxy, e)
                    exam = None
                    exams = None
                    sleep(random.uniform(*self.delay_range_cant_reserve))
                except NetworkException as e:
                    self.errored.emit(user, proxy, e)
                    # proxy = None
                    sleep(random.uniform(*self.delay_range_network_exception))
                except BadScoreException as e:
                    print(repr(e))
                    if self.max_attempts and refreshes >= self.max_attempts:
                        raise CaptchaCrashException(f'MAX_RETRIES({self.max_attempts})') from e
                    self.errored.emit(user, proxy, e)
                    sleep(random.uniform(*self.delay_range_bad_score))
                    if getattr(self, 'driver', None) is not None:
                        self.driver.refresh()
                    refreshes += 1
                except PlusTardException as e:
                    print(repr(e))
                    exam = None
                    refreshes = 0
                    sleep(random.uniform(*self.delay_range_plus_tard))
                except NoSuchElementException as e:
                    if self.max_attempts and refreshes >= self.max_attempts:
                        raise CaptchaCrashException(f'MAX_RETRIES({self.max_attempts})') from e
                    if getattr(self, 'driver', None) is not None:
                        print(repr(e))
                        sleep(random.uniform(*self.delay_range_page_crash))
                        self.driver.refresh()
                        refreshes += 1
                except PageCrashException as e:
                    # recharger la page
                    if self.max_attempts and refreshes >= self.max_attempts:
                        raise CaptchaCrashException(f'MAX_RETRIES({self.max_attempts})') from e
                    sleep(random.uniform(*self.delay_range_recharger))
                    self.driver.refresh()
                    refreshes += 1
                except Exception as e:
                    if isinstance(e, (NoExamsThisDayException,
                                      CloudFlareException,
                                      PermissionError))\
                    or 'chrome not reachable' in str(e):
                        if e.__class__ is CloudFlareException:
                            cloudflare_attempts += 1
                            if self.max_attempts and cloudflare_attempts >= self.max_attempts:
                                self.errored.emit(user, proxy, e)
                                # proxy = None
                                continue
                        if getattr(self, 'driver', None) is not None:
                            sleep(random.uniform(*self.delay_range_chrome_restart))
                            self.driver.restart()
                    else:
                        print(traceback.format_exc())
                        self.errored.emit(user, proxy, e)                        
        except Exception as e:
            self.crashed.emit(user, proxy, e, traceback.format_exc())
        if getattr(self, 'driver', None) is not None:
            self.driver.quit()
        self.finished.emit(self)
    
    def stop(self):
        if getattr(self, 'driver', None) is not None:
            self.driver.quit()


class Manager(QObject):
    """Thread Manager class"""
    log = pyqtSignal(str)
    got_result = pyqtSignal(dict, str, Exception)
    # exception_handled = pyqtSignal(exc, str)
    thread_crashed = pyqtSignal(Exception, str)
    finished = pyqtSignal(int)
    user_started = pyqtSignal(dict)
    booked = pyqtSignal(dict, str, Exam)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.threads = []
        self.count_booked = 0
        self.done_users = []
        self.users = []

    def run(self, *args, users: List[dict], max_threads: int = 1, 
            **kwargs):
        "Start threads to book exams"
        self.users = users

        self.log.emit('Started')
        self.log.emit('Connecting to chrome...')

        for user in self.users[:max_threads]:
            worker = Worker(user, *args, **kwargs)
            worker.booked.connect(
                lambda user,proxy,exam:
                [
                 # self.booked.emit(user, proxy, exam),
                 self.got_result.emit(
                    user, proxy,
                    Exception(f'Success! {exam!r}')),
                 setattr(self, 'count_booked',
                         self.count_booked+1)])
            worker.errored.connect(self.got_result.emit)
            worker.crashed.connect(
                lambda user,proxy,exc,trace:
                [self.got_result.emit(user, proxy, exc),
                 self.thread_crashed.emit(exc, trace)])
            worker.finished.connect(self.continue_thread)
            worker.start()
            self.threads.append(worker)
            self.user_started.emit(user)

    def continue_thread(self, thread: Worker):
        "change user for a thread"
        self.done_users += [thread.user]
        if all(user in self.done_users
                for user in self.users):
            self.finished.emit(self.count_booked)
            return
        for user in self.users:
            if user not in self.done_users\
            and all(user != t.user
                    for t in self.threads):
                thread.terminate()
                thread.user = user
                thread.start()
                self.user_started.emit(user)
                return

    def stop(self):
        for thread in self.threads:
            thread.terminate()
            thread.stop()
        self.threads = []
        self.done_users = []


class App(QtWidgets.QMainWindow):

    version = 0.17
    DEFAULTS = {
        'autoexec': '',
        'anticaptcha_key': '',
        'buffer_size': 20_000,
        'delay_range_keys': [0.2, 0.5],
        'delay_range_move_over': [0.5, 1.5],
        'click_offset_range': [0, 3],
        'delay_range_on_focus': [0.4, 1],
        'delay_range_on_hover': [0.2, 0.4],
        'delay_range_await_exam': [20,30],
        'delay_range_unable_to_login': [0,0],
        'delay_range_cant_reserve': [0,0],
        'delay_range_network_exception': [0,0],
        'delay_range_bad_score': [0,0],
        'delay_range_plus_tard': [0,0],
        'delay_range_page_crash': [0,0],
        'delay_range_recharger': [0,0],
        'delay_range_chrome_restart': [0,0],
        'max_attempts': 0,
        'path_style': 'files\\style.qss',
        'path_user_data': os.environ['USERPROFILE']
        + '\\AppData\\Local\\Google\\Chrome\\User Data',
        'headless': False,
        'max_threads': 4,
        'expand_important': True,
        'users': []
    }

    def __init__(self):
        super().__init__()

        self.console_buffer = ''
        self.selected_user = None
        self.results = {}
        self.load_settings()
        self.manager = Manager()
        self.manager.log.connect(self.log)
        self.manager.got_result.connect(self.on_result)
        self.manager.user_started.connect(
            lambda u:
            [self.log('Started User: ' + u['username']),
             self.update_tree()])
        self.manager.thread_crashed.connect(
            lambda exc,trace:
            [QtWidgets.QMessageBox.critical(
                self, exc.__class__.__name__, trace),
             self.log(f'Error:{exc!r}:{trace}')])
        self.manager.finished.connect(
            lambda count:
            [QtWidgets.QMessageBox.information(
                self, 'Booking bot finished!',
                (f'Successfully Booked {count} exams.'
                 if count
                 else 'No exams Booked!')),
             self.log(f'Info:Finished:{count}'),
             self.update_ui(),
             self.update_tree()])

        self.setup_ui()
        self.setWindowTitle('IFA Booking bot')
        self.setWindowIcon(QIcon('files/img/favicon.ico'))
        self.update_ui()
        self.update_tree()
        if 'chromedriver.exe' not in os.listdir(DIR):
            down_path = ChromeDriverManager().install()
            with open(down_path, 'rb') as fin,\
            open(DIR + '\\chromedriver.exe', 'wb') as fout:
                fout.write(fin.read())
        self.show()

    def setup_ui(self):
        uic.loadUi(UI_PATH, self)
        self.frame_console.hide()
        self.log(f'IFA bot {self.version}')
        self.lbl_version.setText(f'v{self.version}')
        
        # delay_range_keys
        self.lbl_delay_keys = QtWidgets.QLabel(text="Keys Delay")
        self.lbl_delay_keys.setToolTip("Delay between each key press")
        self.layout_delay_settings.addWidget(self.lbl_delay_keys)
        self.rangeedit_delay_keys = RangeEditFloat()
        self.rangeedit_delay_keys.valueChanged.connect(
            lambda v: 
            [setattr(self, 'delay_range_keys', v),
             self.save_settings()])
        self.layout_delay_settings.addWidget(self.rangeedit_delay_keys)
        # delay_range_move_over
        self.lbl_delay_move_over = QtWidgets.QLabel(text="Mouse move time")
        self.lbl_delay_move_over.setToolTip("How long to perform mouse moves")
        self.layout_delay_settings.addWidget(self.lbl_delay_move_over)
        self.rangeedit_delay_move_over = RangeEditFloat()
        self.rangeedit_delay_move_over.valueChanged.connect(
            lambda v: 
            [setattr(self, 'delay_range_move_over', v),
             self.save_settings()])
        self.layout_delay_settings.addWidget(self.rangeedit_delay_move_over)
        # delay_range_on_hover
        self.lbl_delay_on_hover = QtWidgets.QLabel(text="Mouse after hover delay")
        self.lbl_delay_on_hover.setToolTip("Delay after mouse hovered an element")
        self.layout_delay_settings.addWidget(self.lbl_delay_on_hover)
        self.rangeedit_delay_on_hover = RangeEditFloat()
        self.rangeedit_delay_on_hover.valueChanged.connect(
            lambda v: 
            [setattr(self, 'delay_range_on_hover', v),
             self.save_settings()])
        self.layout_delay_settings.addWidget(self.rangeedit_delay_on_hover)
        # click_offset_range
        self.lbl_click_offset = QtWidgets.QLabel(text="Mouse move offset")
        self.lbl_click_offset.setToolTip("Mouse click position randomize in px")
        self.layout_delay_settings.addWidget(self.lbl_click_offset)
        self.rangeedit_click_offset = RangeEdit()
        self.rangeedit_click_offset.valueChanged.connect(
            lambda v: 
            [setattr(self, 'click_offset_range', v),
             self.save_settings()])
        self.layout_delay_settings.addWidget(self.rangeedit_click_offset)
        # delay_range_on_focus
        self.lbl_delay_on_focus = QtWidgets.QLabel(text="Input changed delay")
        self.lbl_delay_on_focus.setToolTip("Delay after input field changed(example: username, password)")
        self.layout_delay_settings.addWidget(self.lbl_delay_on_focus)
        self.rangeedit_delay_on_focus = RangeEditFloat()
        self.rangeedit_delay_on_focus.valueChanged.connect(
            lambda v: 
            [setattr(self, 'delay_range_on_focus', v),
             self.save_settings()])
        self.layout_delay_settings.addWidget(self.rangeedit_delay_on_focus)
        # delay_range_await_exam
        self.lbl_delay_range_await_exam = QtWidgets.QLabel(text="Delay await exam")
        self.lbl_delay_range_await_exam.setToolTip("How often to check for exam if no exams match filters.")
        self.layout_delay_settings.addWidget(self.lbl_delay_range_await_exam)
        self.rangeedit_delay_range_await_exam = RangeEditFloat()
        self.rangeedit_delay_range_await_exam.valueChanged.connect(
            lambda v: 
            [setattr(self, 'delay_range_await_exam', v),
             self.save_settings()])
        self.layout_delay_settings.addWidget(self.rangeedit_delay_range_await_exam)
        # delay_range_unable_to_login
        self.lbl_delay_range_unable_to_login = QtWidgets.QLabel(text="Delay retry login")
        self.lbl_delay_range_unable_to_login.setToolTip("How often to retry login")
        self.layout_delay_settings.addWidget(self.lbl_delay_range_unable_to_login)
        self.rangeedit_delay_range_unable_to_login = RangeEditFloat()
        self.rangeedit_delay_range_unable_to_login.valueChanged.connect(
            lambda v: 
            [setattr(self, 'delay_range_unable_to_login', v),
             self.save_settings()])
        self.layout_delay_settings.addWidget(self.rangeedit_delay_range_unable_to_login)
        # delay_range_cant_reserve
        self.lbl_delay_range_cant_reserve = QtWidgets.QLabel(text="Delay Cant reserve")
        self.lbl_delay_range_cant_reserve.setToolTip("Delay after user got ImpossibleToReserveException(35 days)")
        self.layout_delay_settings.addWidget(self.lbl_delay_range_cant_reserve)
        self.rangeedit_delay_range_cant_reserve = RangeEditFloat()
        self.rangeedit_delay_range_cant_reserve.valueChanged.connect(
            lambda v: 
            [setattr(self, 'delay_range_cant_reserve', v),
             self.save_settings()])
        self.layout_delay_settings.addWidget(self.rangeedit_delay_range_cant_reserve)
        # delay_range_network_exception
        self.lbl_delay_range_network_exception = QtWidgets.QLabel(text="Delay bad network")
        self.lbl_delay_range_network_exception.setToolTip("Delay after proxy connection failed")
        self.layout_delay_settings.addWidget(self.lbl_delay_range_network_exception)
        self.rangeedit_delay_range_network_exception = RangeEditFloat()
        self.rangeedit_delay_range_network_exception.valueChanged.connect(
            lambda v: 
            [setattr(self, 'delay_range_network_exception', v),
             self.save_settings()])
        self.layout_delay_settings.addWidget(self.rangeedit_delay_range_network_exception)
        # delay_range_bad_score
        self.lbl_delay_range_bad_score = QtWidgets.QLabel(text="Delay bad score")
        self.lbl_delay_range_bad_score.setToolTip("Delay after bad score error")
        self.layout_delay_settings.addWidget(self.lbl_delay_range_bad_score)
        self.rangeedit_delay_range_bad_score = RangeEditFloat()
        self.rangeedit_delay_range_bad_score.valueChanged.connect(
            lambda v: 
            [setattr(self, 'delay_range_bad_score', v),
             self.save_settings()])
        self.layout_delay_settings.addWidget(self.rangeedit_delay_range_bad_score)
        # delay_range_plus_tard
        self.lbl_delay_range_plus_tard = QtWidgets.QLabel(text="Delay plus tard")
        self.lbl_delay_range_plus_tard.setToolTip("Delay after plus tard error")
        self.layout_delay_settings.addWidget(self.lbl_delay_range_plus_tard)
        self.rangeedit_delay_range_plus_tard = RangeEditFloat()
        self.rangeedit_delay_range_plus_tard.valueChanged.connect(
            lambda v: 
            [setattr(self, 'delay_range_plus_tard', v),
             self.save_settings()])
        self.layout_delay_settings.addWidget(self.rangeedit_delay_range_plus_tard)
        # delay_range_page_crash
        self.lbl_delay_range_page_crash = QtWidgets.QLabel(text="Delay page crash")
        self.lbl_delay_range_page_crash.setToolTip("Delay after page crashed")
        self.layout_delay_settings.addWidget(self.lbl_delay_range_page_crash)
        self.rangeedit_delay_range_page_crash = RangeEditFloat()
        self.rangeedit_delay_range_page_crash.valueChanged.connect(
            lambda v: 
            [setattr(self, 'delay_range_page_crash', v),
             self.save_settings()])
        self.layout_delay_settings.addWidget(self.rangeedit_delay_range_page_crash)
        # delay_range_recharger
        self.lbl_delay_range_recharger = QtWidgets.QLabel(text="Delay recharger")
        self.lbl_delay_range_recharger.setToolTip("Delay after recharger page error")
        self.layout_delay_settings.addWidget(self.lbl_delay_range_recharger)
        self.rangeedit_delay_range_recharger = RangeEditFloat()
        self.rangeedit_delay_range_recharger.valueChanged.connect(
            lambda v: 
            [setattr(self, 'delay_range_recharger', v),
             self.save_settings()])
        self.layout_delay_settings.addWidget(self.rangeedit_delay_range_recharger)
        # delay_range_chrome_restart
        self.lbl_delay_range_chrome_restart = QtWidgets.QLabel(text="Delay chrome restart")
        self.lbl_delay_range_chrome_restart.setToolTip("Delay after chrome crashed")
        self.layout_delay_settings.addWidget(self.lbl_delay_range_chrome_restart)
        self.rangeedit_delay_range_chrome_restart = RangeEditFloat()
        self.rangeedit_delay_range_chrome_restart.valueChanged.connect(
            lambda v: 
            [setattr(self, 'delay_range_chrome_restart', v),
             self.save_settings()])
        self.layout_delay_settings.addWidget(self.rangeedit_delay_range_chrome_restart)
        # max_attempts
        self.lbl_max_attempts = QtWidgets.QLabel(text="Max Attempts")
        self.lbl_max_attempts.setToolTip("How many attempts a bot have for 'log in' and 'click on exam element'\nSet to '0' to do infinite attempts")
        self.layout_delay_settings.addWidget(self.lbl_max_attempts)
        self.spin_max_attempts = QtWidgets.QSpinBox()
        self.spin_max_attempts.setMinimum(0)
        self.layout_delay_settings.addWidget(self.spin_max_attempts)
        self.spin_max_attempts.valueChanged.connect(
            lambda v:
            [setattr(self, 'max_attempts', v),
             self.save_settings()])
             
        if os.path.isfile(self.path_style):
            with open(self.path_style, encoding=ENCODING) as f:
                self.setStyleSheet(f.read())

        self.input_console.returnPressed.connect(self.exec_command)
        self.btn_console.pressed.connect(self.toggle_console)
        self.btn_copy_log.pressed.connect(self.copy_log)
        self.btn_add_proxy.pressed.connect(self.add_proxy)
        self.btn_remove_all_proxys.pressed.connect(self.remove_all_proxys)
        self.btn_remove_selected_proxy.pressed.connect(
            self.remove_selected_proxy)
        self.btn_add_user.pressed.connect(self.add_user)
        self.btn_edit_user.pressed.connect(self.edit_user)
        self.btn_remove_all_users.pressed.connect(self.remove_all_users)
        self.btn_remove_selected_user.pressed.connect(
            self.remove_selected_user)
        self.btn_save.pressed.connect(self.save)
        self.btn_start.pressed.connect(self.start)        
        self.btn_stop.pressed.connect(
            lambda:
            [self.manager.stop(),
             self.update_ui(),
             self.update_tree()])
        self.dateedit_from.dateChanged.connect(
            lambda d:
            [self.selected_user.update({
                'date_from': attrgetter('year', 'month', 'day')(
                    d.toPyDate())}),
             self.save_settings()])
        self.dateedit_to.dateChanged.connect(
            lambda d:
            [self.selected_user.update({
                'date_to': attrgetter('year', 'month', 'day')(d.toPyDate())}),
             self.save_settings()])
        self.timeedit_from.timeChanged.connect(
            lambda d:
            [self.selected_user.update({
                'time_from': attrgetter('hour', 'minute')(d.toPyTime())}),
             self.save_settings()])
        self.timeedit_to.timeChanged.connect(
            lambda d:
            [self.selected_user.update({
                'time_to': attrgetter('hour', 'minute')(d.toPyTime())}),
             self.save_settings()])
        self.combo_type.currentIndexChanged.connect(
            lambda i:
            [self.selected_user.update({
                'type': self.combo_type.itemText(i)}),
             self.save_settings()])
        self.list_proxys.currentItemChanged.connect(
            lambda _:
            self.btn_remove_selected_proxy.setEnabled(
                self.list_proxys.currentRow() > -1))
        self.list_proxys.itemDoubleClicked.connect(
            lambda _: self.edit_proxy())
        self.tree.itemClicked.connect(
            lambda item:
            [item is not None
                and any(u['username'] == item.text(0)
                        for u in self.users)
                and setattr(self,
                            'selected_user',
                            [u
                             for u in self.users
                             if u['username'] == item.text(0)][0]),
             self.update_ui()])
        self.check_headless.toggled.connect(
            lambda v:
            [setattr(self, 'headless', v),
             self.save_settings()])
        self.slider_max_threads.valueChanged.connect(
            lambda v:
            [setattr(self, 'max_threads', v),
             setattr(self.manager, 'max_threads', v),
             self.save_settings()])
        self.input_anticaptcha_key.textChanged.connect(
            lambda text:
            [setattr(self, 'anticaptcha_key', text),
             self.save_settings()]
        )
        self.btn_clear_cache.pressed.connect(
            lambda: (os.path.isdir(os.environ['USERPROFILE'] + "\\AppData\\Local\\Google\\Chrome\\User Data\\Isolated")
                     and shutil.rmtree(os.environ['USERPROFILE'] + "\\AppData\\Local\\Google\\Chrome\\User Data\\Isolated")))

    def update_ui(self):
        user = self.selected_user
        if user:
            self.lbl_useragent.setText(user['user-agent'])
            self.dateedit_from.setDate(QDate(*user['date_from']))
            self.dateedit_to.setDate(QDate(*user['date_to']))
            self.timeedit_from.setTime(QTime(*user['time_from']))
            self.timeedit_to.setTime(QTime(*user['time_to']))
            for i in range(self.combo_type.count()):
                if self.combo_type.itemText(i) == user['type']:
                    self.combo_type.setCurrentIndex(i)
                    break
            else:
                QtWidgets.QMessageBox.critical(
                    self, 'Error!', f'Bad type: {user["type"]}')
            if user['proxys']:
                self.list_proxys.show()
                self.list_proxys.clear()
                for proxy in user['proxys']:
                    self.list_proxys.addItem(proxy)
            else:
                self.list_proxys.hide()
        else:
            self.lbl_useragent.setText('')
            self.list_proxys.hide()
        self.frame_user_settings.setEnabled(user is not None)

        if any(t.isRunning() for t in self.manager.threads):
            self.btn_stop.show()
            self.btn_start.hide()
        else:
            self.btn_stop.hide()
            self.btn_start.show()
            self.btn_start.setEnabled(bool(self.users))
        self.btn_edit_user.setEnabled(user is not None)
        self.btn_remove_all_users.setEnabled(bool(self.users))
        self.btn_remove_selected_user.setEnabled(user is not None)
        self.btn_remove_all_proxys.setEnabled(bool(user and user['proxys']))
        self.btn_remove_selected_proxy.setEnabled(
            self.list_proxys.currentRow() > -1)
        self.btn_clear_cache.setEnabled(not self.manager.threads)
        self.check_headless.setChecked(self.headless)
        self.slider_max_threads.setValue(self.max_threads)
        self.lbl_max_threads.setNum(self.max_threads)
        self.input_anticaptcha_key.setText(self.anticaptcha_key)
        self.spin_max_attempts.setValue(self.max_attempts)
        self.rangeedit_delay_keys.setValue(self.delay_range_keys)
        self.rangeedit_delay_move_over.setValue(self.delay_range_move_over)
        self.rangeedit_click_offset.setValue(self.click_offset_range)
        self.rangeedit_delay_on_hover.setValue(self.delay_range_on_hover)
        self.rangeedit_delay_on_focus.setValue(self.delay_range_on_focus)
        self.rangeedit_delay_range_await_exam.setValue(self.delay_range_await_exam)
        self.rangeedit_delay_range_unable_to_login.setValue(self.delay_range_unable_to_login)
        self.rangeedit_delay_range_cant_reserve.setValue(self.delay_range_cant_reserve)
        self.rangeedit_delay_range_network_exception.setValue(self.delay_range_network_exception)
        self.rangeedit_delay_range_bad_score.setValue(self.delay_range_bad_score)
        self.rangeedit_delay_range_plus_tard.setValue(self.delay_range_plus_tard)
        self.rangeedit_delay_range_page_crash.setValue(self.delay_range_page_crash)
        self.rangeedit_delay_range_recharger.setValue(self.delay_range_recharger)
        self.rangeedit_delay_range_chrome_restart.setValue(self.delay_range_chrome_restart)

    def update_tree(self):
        self.tree.clear()
        for user in self.users:
            user_node = QtWidgets.QTreeWidgetItem(
                        [user['username']])
            user_status = None
            for proxy in (user['proxys'] or ['']):
                proxy_node = QtWidgets.QTreeWidgetItem([proxy or 'Direct'])
                results = self.results.get((user['username'], proxy))
                if not results:
                    continue
                proxy_status = None
                for result in results:
                    result_node = QtWidgets.QTreeWidgetItem([repr(result)])
                    if str(result).startswith('Success'):
                        result_node.setIcon(0, QIcon('files/img/checked.png'))
                        proxy_status = 'checked'
                        user_status = 'checked'
                    elif is_critical_exception(result):
                        result_node.setIcon(0, QIcon('files/img/close.png'))
                        if proxy_status != 'checked':
                            proxy_status = 'close'
                        if user_status != 'checked':
                            user_status = 'close'
                    else:
                        result_node.setIcon(0, QIcon('files/img/warning.png'))
                        if not proxy_status:
                            proxy_status = 'warning'
                        if not user_status:
                            user_status = 'warning'
                    proxy_node.addChild(result_node)
                if proxy_status:
                    proxy_node.setIcon(
                        0, QIcon(f'files/img/{proxy_status}.png'))
                user_node.addChild(proxy_node)
            if any(user == t.user and t.isRunning()
                    for t in self.manager.threads):
                user_node.setIcon(0, QIcon('files/img/play.png'))
            elif user_status:
                user_node.setIcon(0, QIcon(f'files/img/{user_status}.png'))
            else:
                user_node.setIcon(
                    0, QIcon(r'files/img/user.png'))
            self.tree.addTopLevelItem(user_node)
            if self.expand_important and user_status in ('close', 'checked'):
                user_node.setExpanded(True)

    def on_result(self, user: dict, proxy: str, res: Exception):
        self.log(repr(res))
        name = user['username']
        if (name, proxy) not in self.results:
            self.results[name, proxy] = []
        self.results[name, proxy].append(res)
        self.update_tree()

    def log(self, msg: Any):
        msg = str(msg)
        with open(DIR+'\\main.log', 'a', encoding=ENCODING) as f:
            f.write(f'{datetime.now():%H:%M:%S}:{msg}\n')
        self.console_buffer = (self.console_buffer + msg + '\n'
                               )[-self.buffer_size:]
        self.text_console.setText(self.console_buffer)
        scroll = self.text_console.verticalScrollBar()
        scroll.setValue(scroll.maximum())

    @auto_save
    def set(self, k, v):
        setattr(self, k, v)

    @auto_save
    def toggle_console(self):
        if self.frame_console.isVisible():
            self.frame_console.hide()
        else:
            self.frame_console.show()
            self.input_console.setFocus()

    def load_settings(self):
        if not os.path.isfile(CONFIG_PATH):
            for k,v in self.DEFAULTS.items():
                if v.__class__ in (list, dict):
                    setattr(self, k, v.copy())
                else:
                    setattr(self, k, v)
            return
        try:
            with open(CONFIG_PATH, encoding=ENCODING) as f:
                cfg = json.load(f)
        except json.decoder.JSONDecodeError as e:
            print(repr(e))
            if os.path.isfile(CONFIG_PATH):
                try:
                    os.remove(CONFIG_PATH)
                except Exception as e:
                    print(repr(e))
            cfg = self.DEFAULTS
        for k,v in self.DEFAULTS.items():
            if v.__class__ in (list, dict):
                setattr(self, k, cfg.get(k, v.copy()))
            else:
                setattr(self, k, cfg.get(k, v))
        for user in self.users:
            user.setdefault('user-agent', random.choice(USER_AGENTS))
            user.setdefault('proxys', [])
            user.setdefault('profile', "Default")

    def save_settings(self):
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        d = {}
        for k,v in self.DEFAULTS.items():
            var = getattr(self, k)
            if v != var:
                d[k] = var
        if d:
            with open(CONFIG_PATH, 'w', encoding=ENCODING) as f:
                json.dump(d, f, indent=4)
        elif os.path.isfile(CONFIG_PATH):
            # deleting config if settings == defaults
            os.remove(CONFIG_PATH)

    @auto_save
    def exec_command(self):
        command = self.input_console.text()
        self.input_console.clear()
        if not command:
            pass
        elif command == 'cls':
            self.console_buffer = ''
            self.log('')
        else:
            self.log('>>> '+command)
            try:
                res = eval(
                    command, globals(),
                    {k:getattr(self,k) for k in dir(self)})
                if res is not None:
                    self.log(pformat(res))
            except Exception:
                self.log(traceback.format_exc())
            self.update_ui()

    @auto_save
    def copy_log(self, *_):
        QtWidgets.QApplication.clipboard().setText(self.console_buffer)

    @auto_save
    def add_user(self, *_):
        dialog = UserEditDialog()
        if dialog.exec_() and dialog.username:
            user = {'username': dialog.username,
                    'password': dialog.password,
                    'date_from': list(attrgetter('year', 'month', 'day')(
                                QDate.currentDate().addDays(1).toPyDate())),
                    'date_to': list(attrgetter('year', 'month', 'day')(
                                QDate.currentDate().addDays(1).addMonths(1)
                                    .toPyDate())),
                    'time_from': [0, 0],
                    'time_to': [23, 59],
                    'type': 'All',
                    'proxys': [],
                    'profile': dialog.profile,
                    'user-agent': random.choice(USER_AGENTS)
                    }
            self.users.append(user)
            self.selected_user = user
        self.update_ui()
        self.update_tree()

    @auto_save
    def edit_user(self):
        dialog = UserEditDialog(self.selected_user['username'],
                                self.selected_user['password'],
                                self.selected_user['profile'])
        if dialog.exec_() and dialog.username:
            self.selected_user['username'] = dialog.username
            self.selected_user['password'] = dialog.password
            self.selected_user['profile'] = dialog.profile
        self.update_ui()
        self.update_tree()

    @auto_save
    def remove_all_users(self, *_):
        self.users = []
        self.selected_user = None
        self.update_ui()
        self.update_tree()

    @auto_save
    def remove_selected_user(self, *_):
        self.users.remove(self.selected_user)
        self.selected_user = None
        self.update_ui()
        self.update_tree()

    @try_handle
    @auto_save
    def add_proxy(self, *_):
        dialog = ProxyEditDialog()
        if dialog.exec_():
            if re.search(PROXY_PATTERN, dialog.proxy):
                self.selected_user['proxys'].append(dialog.proxy)
            else:
                QtWidgets.QMessageBox.critical(
                    self, 'Bad proxy!',
                    'Proxy should look like this:\n'
                    '192.168.49.120:8080\n'
                    'Or this:\n'
                    'ab-proxy-sample.company.com:8080\n'
                    f'Not {dialog.proxy!r}')
        self.update_ui()

    @try_handle
    @auto_save
    def edit_proxy(self, index: int = None):
        if index is None:
            index = self.list_proxys.currentRow()
        dialog = ProxyEditDialog(self.selected_user['proxys'][index])
        while True:
            if dialog.exec_():
                if re.search(PROXY_PATTERN, dialog.proxy):
                    self.selected_user['proxys'][index] = dialog.proxy
                else:
                    QtWidgets.QMessageBox.critical(
                        self, 'Bad proxy!',
                        'Proxy should look like this:\n'
                        '192.168.49.120:8080\n'
                        'Or this:\n'
                        'ab-proxy-sample.company.com:8080\n'
                        f'Not {dialog.proxy!r}')
                    continue
            break
        self.update_ui()

    @auto_save
    def remove_all_proxys(self, *_):
        self.selected_user['proxys'] = []
        self.update_ui()

    @auto_save
    def remove_selected_proxy(self, *_):
        self.selected_user['proxys'].pop(self.list_proxys.currentRow())
        self.update_ui()

    @try_handle
    def start(self):
        self.manager.stop()
        self.results = {}
        self.manager.run(
            users=self.users, anticaptcha_key=self.anticaptcha_key,
            headless=self.headless, max_threads=self.max_threads,
            delay_range_keys = self.delay_range_keys,
            delay_range_move_over = self.delay_range_move_over,
            click_offset_range = self.click_offset_range,
            delay_range_on_focus = self.delay_range_on_focus,
            delay_range_on_hover = self.delay_range_on_hover,
            delay_range_await_exam=self.delay_range_await_exam,
            delay_range_unable_to_login=self.delay_range_unable_to_login,
            delay_range_cant_reserve=self.delay_range_cant_reserve,
            delay_range_network_exception=self.delay_range_network_exception,
            delay_range_bad_score=self.delay_range_bad_score,
            delay_range_plus_tard=self.delay_range_plus_tard,
            delay_range_page_crash=self.delay_range_page_crash,
            delay_range_recharger=self.delay_range_recharger,
            delay_range_chrome_restart=self.delay_range_chrome_restart,
            max_attempts=self.max_attempts)
        self.update_ui()
        self.update_tree()

    def save(self, path: str = None):
        try:
            files = [
                DIR + '\\results\\' + i
                for i in os.listdir('results')
                if os.path.isfile(DIR + '\\results\\' + i)]
            if not files:
                QtWidgets.QMessageBox.information(
                    self, 'Nothing to save', "No exam pdf's saved!")
                return
            if path is None:
                options = QtWidgets.QFileDialog.Options()
                path, _res = QtWidgets.QFileDialog.getSaveFileName(
                                self,
                                "Save results",
                                os.environ['USERPROFILE']
                                + "\\downloads\\results.pdf",
                                "PDF Files (*.pdf)",
                                options=options)
            if path:
                html2pdf(files, path)
                try:
                    os.startfile(path, 'print')
                except OSError:
                    # If no default program for printing PDF's
                    os.startfile(path)
            self.update_ui()
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self, e.__class__.__name__, traceback.format_exc())


def main() -> int:
    os.chdir(DIR)
    if os.path.exists(DIR + '\\results'):
        for item in os.listdir(DIR + '\\results'):
            if os.path.isfile(DIR + '\\results\\' + item):
                os.remove(DIR + '\\results\\' + item)
    else:
        os.mkdir(DIR + '\\results')
    app = QtWidgets.QApplication(sys.argv)
    _ = App()
    return app.exec_()


if __name__ == '__main__':
    try:
        if '--debug' not in sys.argv:
            user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
        main()
        if '--debug' not in sys.argv:
            user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 1)
    except Exception as e:
        text = traceback.format_exc()
        user32.MessageBoxW(0, text, e.__class__.__name__, 0x0)
        kill_task('chromedriver.exe')
        if '--debug' not in sys.argv:
            user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 1)
        raise e
