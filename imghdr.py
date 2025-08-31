"""Custom imghdr implementation for Python 3.13 compatibility"""
import os

def what(file, h=None):
    """Determine the type of an image file."""
    if h is None:
        with open(file, 'rb') as f:
            h = f.read(32)

    if h.startswith(b'\xff\xd8\xff'):
        return 'jpeg'
    elif h.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    elif h.startswith(b'GIF87a') or h.startswith(b'GIF89a'):
        return 'gif'
    elif h.startswith(b'BM'):
        return 'bmp'
    elif h.startswith(b'RIFF') and h[8:12] == b'WEBP':
        return 'webp'
    elif h.startswith(b'\x00\x00\x01\x00'):
        return 'ico'
    elif h.startswith(b'\x49\x49\x2a\x00') or h.startswith(b'\x4d\x4d\x00\x2a'):
        return 'tiff'

    return None
