from zipfile import ZipFile
from xml.etree import ElementTree as ET
from collections import Counter
import re, math

ODS = '/mnt/data/KIM_0037621244_9(1).ods'
ODT = '/mnt/data/KIM_0037621244_10.odt'

# ---------- Task 9 ----------
def task9_count_rows(path):
    ns_table = '{urn:oasis:names:tc:opendocument:xmlns:table:1.0}'
    ns_office = '{urn:oasis:names:tc:opendocument:xmlns:office:1.0}'
    with ZipFile(path) as z:
        root = ET.fromstring(z.read('content.xml'))
    table = root.find('.//' + ns_table + 'table')
    answer = 0
    data_rows = 0
    for row in table.findall(ns_table + 'table-row'):
        vals = []
        for cell in row.findall(ns_table + 'table-cell'):
            repeat = int(cell.attrib.get(ns_table + 'number-columns-repeated', '1'))
            val = cell.attrib.get(ns_office + 'value')
            if val is None:
                val = ''.join(cell.itertext()).strip() or None
            if len(vals) < 6:
                vals.extend([val] * min(repeat, 6 - len(vals)))
            else:
                break
        if len(vals) < 6 or all(v in (None, '') for v in vals):
            continue
        try:
            nums = [int(float(v)) for v in vals[:6]]
        except ValueError:
            continue
        data_rows += 1
        c = Counter(nums)
        # ровно одно число встречается дважды, остальные четыре различны
        if sorted(c.values(), reverse=True) != [2, 1, 1, 1, 1]:
            continue
        repeated = next(n for n, k in c.items() if k == 2)
        product_unique = math.prod(n for n, k in c.items() if k == 1)
        if repeated ** 3 < product_unique:
            answer += 1
    return answer, data_rows

# ---------- Task 10 ----------
def task10_count_da(path):
    with ZipFile(path) as z:
        root = ET.fromstring(z.read('content.xml'))
    text = ' '.join(root.itertext())
    text = re.sub(r'\s+', ' ', text)
    # отдельное слово Да/да, не часть другого слова
    pattern = r'(?<![А-Яа-яЁёA-Za-z])(?:Да|да)(?![А-Яа-яЁёA-Za-z])'
    return len(re.findall(pattern, text))

# ---------- Task 12 ----------
def task12_turing():
    # лента: число 127 = 1111111, голова на ближайшей справа пустой клетке
    tape = {i: '1' for i in range(7)}
    head = 7
    state = 'q0'
    def read(pos): return tape.get(pos, 'λ')
    def write(pos, sym):
        if sym == 'λ':
            tape.pop(pos, None)
        else:
            tape[pos] = sym
    program = {
        ('q0', 'λ'): ('λ', -1, 'q1'),
        ('q1', 'λ'): ('1', +1, 'q2'),
        ('q1', '0'): ('1', +1, 'q2'),
        ('q1', '1'): ('0', -1, 'q1'),
        ('q2', 'λ'): ('1', 0, 'HALT'),
        ('q2', '0'): ('0', +1, 'q2'),
        ('q2', '1'): ('1', +1, 'q2'),
    }
    while state != 'HALT':
        sym = read(head)
        new_sym, move, new_state = program[(state, sym)]
        write(head, new_sym)
        head += move
        state = new_state
    lo, hi = min(tape), max(tape)
    bits = ''.join(tape.get(i, 'λ') for i in range(lo, hi + 1)).strip('λ')
    return bits, int(bits, 2)

# ---------- Task 11 ----------
def task11_max_length():
    alphabet = 10 + 120
    bits_per_char = math.ceil(math.log2(alphabet))
    count_numbers = 1_275_526
    limit_bytes = 5 * 1024**3
    max_len = 0
    for length in range(1, 100000):
        bytes_per_number = math.ceil(bits_per_char * length / 8)
        if bytes_per_number * count_numbers <= limit_bytes:
            max_len = length
        else:
            break
    return max_len, bits_per_char

# ---------- Task 13 ----------
def task13_broadcast_sum():
    ip = [154, 141, 198, 190]
    mask = [255, 255, 192, 0]
    broadcast = [(ip[i] & mask[i]) | (255 ^ mask[i]) for i in range(4)]
    return broadcast, sum(broadcast)

# ---------- Task 14 ----------
def from_base_22(digits):
    value = 0
    for d in digits:
        value = value * 22 + d
    return value

def task14():
    suitable = []
    for x in range(22):
        value = (
            from_base_22([2, 7, x, 9, 8, 9, 6, 6]) +
            from_base_22([2, 6, x, 3, 3]) +
            from_base_22([5, 2, 2, x, 5])
        )
        if value % 21 == 0:
            suitable.append((x, value // 21))
    return suitable[0], suitable

# ---------- Task 15 ----------
def task15():
    # формула ложна только если x кратно 33 и 242, но не кратно A.
    # Поэтому A должен делить НОК(33,242); максимум равен НОК.
    lcm = math.lcm(33, 242)
    # brute-check around answer
    def divides(x, m): return x % m == 0
    def formula_true_for_all_checked(A, limit=20000):
        for x in range(1, limit + 1):
            left = divides(x, 33)
            inner_left = not divides(x, A)
            inner_right = not divides(x, 242)
            value = (not left) or ((not inner_left) or inner_right)
            if not value:
                return False, x
        return True, None
    return lcm, formula_true_for_all_checked(lcm)

if __name__ == '__main__':
    ans9, rows = task9_count_rows(ODS)
    print('9:', ans9, '(строк с данными:', rows, ')')
    print('10:', task10_count_da(ODT))
    print('11:', task11_max_length()[0], '(бит на символ:', task11_max_length()[1], ')')
    bits12, ans12 = task12_turing()
    print('12:', ans12, '(двоично:', bits12, ')')
    bcast, ans13 = task13_broadcast_sum()
    print('13:', ans13, '(broadcast:', '.'.join(map(str, bcast)), ')')
    print('14:', task14()[0][1], '(минимальный x:', task14()[0][0], '; все подходящие:', task14()[1], ')')
    print('15:', task15()[0], '(проверка:', task15()[1], ')')
