import sys
import functools
import inspect
import re

import oauth2 as oauth
import urllib.error
import urllib.parse
import urllib.request

import evernote.edam.userstore.UserStore as UserStore
import evernote.edam.notestore.NoteStore as NoteStore
import evernote.edam.userstore.constants as UserStoreConstants

import thrift.protocol.TBinaryProtocol as TBinaryProtocol
import thrift.transport.THttpClient as THttpClient


class EvernoteClient(object):
    def __init__(self, **options):
        self.consumer_key = options.get('consumer_key')
        self.consumer_secret = options.get('consumer_secret')
        self.sandbox = options.get('sandbox', True)
        self.china = options.get('china', False)
        if self.sandbox:
            default_service_host = 'sandbox.evernote.com'
        elif self.china:
            default_service_host = 'app.yinxiang.com'
        else:
            default_service_host = 'www.evernote.com'
        self.service_host = options.get('service_host', default_service_host)
        self.additional_headers = options.get('additional_headers', {})
        self.token = options.get('token')
        self.secret = options.get('secret')

    def _get_oauth_client(self, token=None):
        consumer = oauth.Consumer(self.consumer_key, self.consumer_secret)
        if token:
            client = oauth.Client(consumer, token)
        else:
            client = oauth.Client(consumer)
        return client

    def get_request_token(self, callback_url):

        client = self._get_oauth_client()
        request_url = '{}?oauth_callback={}'.format(
            self._get_endpoint('oauth'), urllib.parse.quote(callback_url)
        )

        resp, content = client.request(request_url, 'GET')
        request_token = dict(urllib.parse.parse_qsl(content.decode('utf-8')))
        return request_token

    def get_authorize_url(self, request_token):
        return '{}?oauth_token={}'.format(
            self._get_endpoint('OAuth.action'),
            urllib.parse.quote(request_token['oauth_token'])
        )

    def get_access_token(self, oauth_token,
                         oauth_token_secret, oauth_verifier, return_full_dict=False):
        token = oauth.Token(oauth_token, oauth_token_secret)
        token.set_verifier(oauth_verifier)
        client = self._get_oauth_client(token)

        resp, content = client.request(self._get_endpoint('oauth'), 'POST')
        access_token_dict = dict(urllib.parse.parse_qsl(content.decode('utf-8')))
        self.token = access_token_dict['oauth_token']

        if return_full_dict:
            return access_token_dict

        return access_token_dict['oauth_token']

    def get_access_token_dict(self, oauth_token,
                              oauth_token_secret, oauth_verifier):
        """
        Full dict looks like:

         {'edam_shard': 's146',
         'edam_noteStoreUrl': 'https://www.evernote.com/shard/s146/notestore',
         'edam_userId': '19358593',
         'edam_webApiUrlPrefix': 'https://www.evernote.com/shard/s146/',
         'edam_expires': '1481161090922',
         'oauth_token': '...'}

        Unit of expire time is millisecond.
        """
        access_token_dict = self.get_access_token(oauth_token=oauth_token,
                                                  oauth_token_secret=oauth_token_secret,
                                                  oauth_verifier=oauth_verifier,
                                                  return_full_dict=True)
        return access_token_dict

    def get_user_store(self):
        user_store_uri = self._get_endpoint("/edam/user")
        store = Store(self.token, UserStore.Client, user_store_uri)
        if not store:  # Trick for PyDev code completion
            store = UserStore.Client()
            raise Exception('Should never reach here')
        return store

    def get_note_store(self):
        user_store = self.get_user_store()
        note_store_uri = user_store.getNoteStoreUrl()
        store = Store(self.token, NoteStore.Client, note_store_uri)
        if not store:  # Trick for PyDev code completion
            store = NoteStore.Client()
            raise Exception('Should never reach here')
        return store

    def get_shared_note_store(self, linkedNotebook):
        note_store_uri = linkedNotebook.noteStoreUrl
        note_store = Store(self.token, NoteStore.Client, note_store_uri)
        shared_auth = note_store.authenticateToSharedNotebook(
            linkedNotebook.shareKey)
        shared_token = shared_auth.authenticationToken
        store = Store(shared_token, NoteStore.Client, note_store_uri)
        if not store:  # Trick for PyDev code completion
            store = NoteStore.Client()
            raise Exception('Should never reach here')
        return store

    def get_business_note_store(self):
        user_store = self.get_user_store()
        biz_auth = user_store.authenticateToBusiness()
        biz_token = biz_auth.authenticationToken
        note_store_uri = biz_auth.noteStoreUrl
        store = Store(biz_token, NoteStore.Client, note_store_uri)
        if not store:  # Trick for PyDev code completion
            store = NoteStore.Client()
            raise Exception('Should never reach here')
        return store

    def _get_endpoint(self, path=None):
        url = "https://%s" % (self.service_host)
        if path is not None:
            url += "/%s" % path
        return url


class Store(object):
    def __init__(self, token, client_class, store_url):
        self.token = token
        m = re.search(':A=(.+):', token)
        if m:
            self._user_agent_id = m.groups()[0]
        else:
            self._user_agent_id = ''
        self._client = self._get_thrift_client(client_class, store_url)

    def __getattr__(self, name):
        def delegate_method(*args, **kwargs):
            targetMethod = getattr(self._client, name, None)
            if targetMethod is None:
                return object.__getattribute__(self, name)(*args, **kwargs)

            org_args = inspect.getargspec(targetMethod).args
            if len(org_args) == len(args) + 1:
                return targetMethod(*args, **kwargs)
            elif 'authenticationToken' in org_args:
                skip_args = ['self', 'authenticationToken']
                arg_names = [i for i in org_args if i not in skip_args]
                return functools.partial(
                    targetMethod, authenticationToken=self.token
                )(**dict(list(zip(arg_names, args))))
            else:
                return targetMethod(*args, **kwargs)

        return delegate_method

    def _get_thrift_client(self, client_class, url):
        http_client = THttpClient.THttpClient(url)
        http_client.setCustomHeaders({
            'User-Agent': "%s / %s; Python / %s;"
                          % (
                self._user_agent_id, self._get_sdk_version(), sys.version.replace("\n", ""))
        })

        thrift_protocol = TBinaryProtocol.TBinaryProtocol(http_client)
        return client_class(thrift_protocol)

    def _get_sdk_version(self):
        return '%s.%s' % (
            UserStoreConstants.EDAM_VERSION_MAJOR,
            UserStoreConstants.EDAM_VERSION_MINOR
        )
