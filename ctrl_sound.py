"""
Messenger Plus sound server implementation

(GET) /esnd/snd/builtin?code={code} -> builtin
(GET) /esnd/snd/check?hash={hash} -> check
(GET) /esnd/snd/get?hash={hash} -> get
(POST) /esnd/snd/put[?uf=1] -> put
"""

from aiohttp.web import Response
from os import path, makedirs


PATHS = {
    'builtins': path.join('storage', 'sound', 'builtins'),
    'users': path.join('storage', 'sound', 'users'),
}


def builtin(request):
    """
    Get builtin sound file

    (GET) /esnd/snd/builtin?code={code}

    :type request: aiohttp.web.Request
    :return file content or 0 if file is not found
    :rtype: aiohttp.web.Response
    """
    file_name = request.rel_url.query['code'] + '.mp3'
    file_path = path.join(PATHS['builtins'], file_name)

    try:
        with open(file_path, 'rb') as file:
            return Response(status=200, content_type="audio/mpeg", body=file.read())

    except FileNotFoundError:
        return Response(status=200, body='0')


def check(request):
    """
    Check if sound file is available

    (GET) /esnd/snd/check?hash={hash}

    :type request: aiohttp.web.Request
    :return 1 if file exists 0 otherwise
    :rtype: aiohttp.web.Response
    """
    file_hash = request.rel_url.query['hash']

    result = int(path.exists(_get_file_path(file_hash)))

    return Response(status=200, body=str(result))


async def put(request):
    """
    Upload new sound file.
    Overwrite existing file if uf=1

    (POST) /esnd/snd/put[?uf=1]

    :type request: aiohttp.web.Request
    :return 1 for success 0 for failure
    :rtype: aiohttp.web.Response
    """
    data = await request.post()

    with data['file'].file as f:
        f.seek(-19, 2)  # hash offset

        file_path = _get_file_path(f.read(12).decode('ascii'))

        try:
            output = open(file_path, 'xb')
        except FileNotFoundError:
            makedirs(path.dirname(file_path))

            output = open(file_path, 'xb')
        except FileExistsError:
            if 'uf' in request.rel_url.query and request.rel_url.query['uf'] == 1:
                output = open(file_path, 'wb')
            else:
                return Response(status=200, body='0')

        f.seek(0)

        with output as o:
            o.write(f.read())

    return Response(status=200, body='1')


def get(request):
    """
    Get sound file

    (GET) /esnd/snd/get?hash={hash}

    :type request: aiohttp.web.Request
    :return file content or 0 if file is not found
    :rtype: aiohttp.web.Response
    """
    file_hash = request.rel_url.query['hash']

    try:
        with open(_get_file_path(file_hash), 'rb') as f:
            return Response(status=200, content_type="audio/mpeg", body=f.read())
    except FileNotFoundError:
        return Response(status=200, body='0')


def _get_file_path(file_hash):
    """
    Get file path by hash.
    Uses first 3 symbols for subdirectories e.g. 12345 -> 1/2/3/12345.mp3

    :type file_hash: string
    :rtype: string
    """
    return path.join(PATHS['users'], *file_hash[:3], file_hash + '.mp3')
