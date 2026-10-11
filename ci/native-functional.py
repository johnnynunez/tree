"""CPU-only native wheel tests; never opens or writes a HID device."""
import importlib
import importlib.metadata
import os
from pathlib import Path
import struct
import sys
import sysconfig
import pytest

PACKAGE = os.environ['NATIVE_PACKAGE']

def test_native_interpreter_and_exact_version():
    assert sys.version_info[:2] == (3, 12)
    assert sysconfig.get_platform() == 'win-arm64'
    assert importlib.metadata.version(PACKAGE) == os.environ['NATIVE_VERSION']

def test_functional_and_negative_controls():
    if PACKAGE == 'PyOpenGL-accelerate':
        import numpy as np
        import OpenGL
        from OpenGL import GL, error
        from OpenGL.arrays.arraydatatype import ArrayDatatype
        from OpenGL_accelerate.numpy_formathandler import NumpyHandler
        assert OpenGL.USE_ACCELERATE
        assert ArrayDatatype.isAccelerated
        module = importlib.import_module('OpenGL_accelerate.numpy_formathandler')
        assert Path(module.__file__).suffix == '.pyd'
        a = np.arange(24, dtype=np.float32).reshape(4, 6)
        handler = NumpyHandler()
        assert handler.dataPointer(a) == a.ctypes.data
        assert handler.arrayByteCount(a) == a.nbytes
        converted = handler.asArray(a, GL.GL_DOUBLE)
        np.testing.assert_array_equal(converted, a)
        assert converted.dtype == np.float64
        strided = a[:, ::2]
        assert not strided.flags.c_contiguous
        with pytest.raises(error.CopyError):
            NumpyHandler(True).asArray(strided)
        contiguous = handler.asArray(strided)
        np.testing.assert_array_equal(contiguous, strided)
        assert contiguous.flags.c_contiguous
        assert handler.dataPointer(contiguous) != strided.ctypes.data
    elif PACKAGE == 'dm-tree':
        import tree
        from collections import namedtuple
        Pair = namedtuple('Pair', 'left right')
        x = {'b': [3, 4], 'a': Pair(1, 2)}
        assert tree.flatten(x) == [1, 2, 3, 4]
        assert tree.unflatten_as(x, [5, 6, 7, 8]) == {'b': [7, 8], 'a': Pair(5, 6)}
        assert tree.map_structure(lambda v: v * 2, x) == {'b': [6, 8], 'a': Pair(2, 4)}
        assert tree.map_structure(lambda a, b: a+b, x, x) == tree.map_structure(lambda a: a*2, x)
        with pytest.raises((ValueError, TypeError)):
            tree.map_structure(lambda a, b: a+b, [1, 2], [3])
        with pytest.raises(ValueError):
            tree.unflatten_as(x, [1])
        assert Path(tree._tree.__file__).suffix == '.pyd'
    else:
        import hid
        assert Path(hid.__file__).suffix == '.pyd'
        devices = hid.enumerate()
        assert isinstance(devices, list)
        for device in devices:
            assert isinstance(device['path'], bytes)
            assert isinstance(device['vendor_id'], int)
            assert isinstance(device['product_id'], int)
        # Cython argument conversion rejects these before entering HIDAPI.
        with pytest.raises(TypeError):
            hid.enumerate(object(), 0)
        with pytest.raises(OverflowError):
            hid.enumerate(1 << 100, 0)
        unopened = hid.device()
        unopened.close()
        unopened.close()

def test_native_extension_inventory():
    dist = importlib.metadata.distribution(PACKAGE)
    paths = [dist.locate_file(f) for f in dist.files if str(f).endswith('.pyd')]
    assert len(paths) == (9 if PACKAGE == 'PyOpenGL-accelerate' else 1)
    for path in paths:
        data = Path(path).read_bytes()
        offset = struct.unpack_from('<I', data, 0x3c)[0]
        assert data[offset:offset+4] == b'PE\0\0'
        assert struct.unpack_from('<H', data, offset+4)[0] == 0xaa64
        assert 'site-packages' in str(path)
