import os
import yaml
from flask import (Flask as BaseFlask, Config as BaseConfig, 
                   render_template, flash)
from functools import wraps
from werkzeug import BaseResponse

POSTGRES_TEMPLATE = 'postgresql://%(username)s:%(password)s@%(host)s:%(port)s/%(database)s'

class Config(BaseConfig):
    
    def from_bundle_config(self):
        try:
            from bundle_config import config
        except ImportError:
            return
        
        if 'postgres' in config:
            self['SQLALCHEMY_DATABASE_URI'] = POSTGRES_TEMPLATE % config['postgres']
            
        self['WEBASSETS_CACHE'] = os.path.join(config['core']['data_directory'], 
                                               '.webassets-cache')
        
    
    def from_yaml(self, root_path):
        env = os.environ.get('FLASK_ENV', 'DEVELOPMENT').upper()
        self['ENVIRONMENT'] = env.lower()
        
        for fn in ('app', 'credentials'):
            config_file = os.path.join(root_path, 'config', '%s.yml' % fn)
            
            with open(config_file) as f:
                c = yaml.load(f)
            
            c = c.get(env, c)
            
            for key in c.iterkeys():
                if key.isupper():
                    self[key] = c[key]
                    
                
class Flask(BaseFlask):
    """Extended version of `Flask` that implements custom config class
    and adds `register_middleware` method"""
    
    def make_config(self, instance_relative=False):
        root_path = self.root_path
        if instance_relative:
            root_path = self.instance_path
        return Config(root_path, self.default_config)
    
    def register_middleware(self, middleware_class):
        """Register a WSGI middleware on the application
        :param middleware_class: A WSGI middleware implementation
        """
        self.wsgi_app = middleware_class(self.wsgi_app)
        