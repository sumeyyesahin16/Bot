import os
import json
from time import time
from typing import Any

from selenium.webdriver import ChromeOptions
from selenium.webdriver import Chrome as _Chrome
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

from .exceptions import NetworkException


CSS = By.CSS_SELECTOR
DIR = os.path.dirname(__file__)
DELAY_PAGE_LOAD = 10
DRIVER_ARGS = [
    'start-maximized',
    'no-default-browser-check',
    'disable-logging',
	'incognito'
]
DRIVER_EXPERIMENTS = [
    'prefs'
]
DRIVER_KWARGS = [
    'proxy-server',
    'user-agent',
    'user-data-dir',
    'profile-directory',
    'remote-debugging-port',
	'host-resolver-rules'
]
DRIVER_ATTRS = [
    'binary_location',
    'headless'
]
ALIASES = {
    'path_chrome': 'binary_location',
    'proxy_server': 'proxy-server',
    'proxy': 'proxy-server',
    'profile_directory': 'profile-directory',
    'profile': 'profile-directory',
    'user_agent': 'user-agent',
    'user_data_dir': 'user-data-dir',
    'remote_debugging_port': 'remote-debugging-port',
	'host_resolver_rules': 'host-resolver-rules'
}


def upgrade(chrome: type) -> type:

    class Chrome(chrome):
        def __init__(self, *args, options=None, anticaptcha_key: str = '', **kwargs):
            args = [(arg if not arg.startswith('--') else arg[2:])
                    for arg in args]
            kwargs.setdefault('headless', True)
            args = [ALIASES.get(arg, arg) for arg in args]
            kwargs = {ALIASES.get(k, k):v for k,v in kwargs.items()}
            
            if options is None:
                options = ChromeOptions()
            for arg in args.copy():
                if arg in DRIVER_ARGS:
                    options.add_argument('--' + arg)
                    args.remove(arg)
            for k,v in kwargs.copy().items():
                if k in DRIVER_KWARGS:
                    options.add_argument(f'--{k}={v}')
                    kwargs.pop(k)
                elif k in DRIVER_ATTRS:
                    setattr(options, k, v)
                    kwargs.pop(k)
                elif k in DRIVER_EXPERIMENTS:
                    options.add_experimental_option(k, v)
                    kwargs.pop(k)

            if anticaptcha_key:
                options.add_extension(DIR + '\\anticaptcha-plugin_v0.61.zip')
            self.args = args
            self.kwargs = kwargs
            self.options = options
            self.anticaptcha_key = anticaptcha_key
            
            # if args and os.path.isfile(args[0]):
            # try:
            super().__init__(*args, options=options, **kwargs)
            if anticaptcha_key:
                try:
                    self.get('https://antcpt.com/blank.html')
                    message = {
                        'receiver': 'antiCaptchaPlugin',
                        'type': 'setOptions',
                        'options': {'antiCaptchaApiKey': anticaptcha_key}
                    }
                    self.execute_script(f"window.postMessage({json.dumps(message)});")
                except Exception as e:
                    self.quit()
                    raise e
            # except Exception:
                # download_path = ChromeDriverManager().install()
                # with open(download_path, 'rb') as fin:
                    # with open('chromedriver.exe', 'wb') as fout:
                        # fout.write(fin.read())
                # super().__init__('chromedriver.exe', *args, options=options, **kwargs)
                

        def restart(self, *args, **kwargs):
            args = args or self.args
            kwargs = {**self.kwargs, **kwargs}

            self.quit()
            self.__init__(*args, options=self.options, anticaptcha_key=self.anticaptcha_key, **kwargs)

        def get(self, *args, **kwargs):
            super().get(*args, **kwargs)
            time_end = time() + DELAY_PAGE_LOAD
            while time() < time_end:
                err_elements = self.find_elements(CSS, '.error-code')
                if not err_elements\
                and self.page_source != '<html><head></head>'\
                                        '<body></body></html>':
                    break
            else:
                if err_elements:
                    raise NetworkException(err_elements[0].text)
                raise NetworkException('UNKNOWN')

        def click(self, elem: 'WebElement'):
            # elem.click()  # Regular clicking Not working
            self.js_click(elem)

        def js_click(self, elem: 'WebElement'):
            self.execute_script("arguments[0].click();", elem)

        def set_attribute(self, elem: 'WebElement', key: str, value: Any):
            self.execute_script(f"arguments[0].setAttribute({key!r}, "\
                                f"{json.dumps(value)});", elem)
    
    return Chrome


def Chrome(*args, **kwargs) -> 'Chrome':
    return upgrade(_Chrome)(*args, **kwargs)