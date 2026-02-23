import vroom._vroom as _vroom_core
import array, struct

# Try every single-char format code to find what _vroom.Matrix accepts
formats = ['b','B','h','H','i','I','l','L','q','Q','f','d','n','N']
flat_9 = [0,1,2,1,0,1,2,1,0]

for fmt in formats:
    try:
        a = array.array(fmt, flat_9)
        m = _vroom_core.Matrix(a)
        print(f"OK: array('{fmt}')  itemsize={a.itemsize}")
    except Exception as e:
        print(f"    FAIL array('{fmt}'): {str(e)[:60]}")
