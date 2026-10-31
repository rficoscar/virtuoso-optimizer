# -*- coding: utf-8 -*-
"""skillparen.py - SKILL 文件括号平衡检查（忽略字符串/注释，支持 \\ 转义）。"""
import sys

BS = chr(92)


def strip_code(text):
    out = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i += 1
            while i < n:
                if text[i] == BS:
                    i += 2
                    continue
                if text[i] == '"':
                    i += 1
                    break
                i += 1
            out.append('""')
            continue
        if c == ';':
            while i < n and text[i] != '\n':
                i += 1
            continue
        out.append(c)
        i += 1
    return ''.join(out)


def top_forms(code):
    """按顶层括号切分，返回 [(line, first_token, text_head)]。"""
    forms = []
    depth = 0
    line = 1
    start = None
    start_line = None
    buf = []
    for c in code:
        if c == '\n':
            line += 1
        if c in '([':
            if depth == 0:
                start = len(buf)
                start_line = line
            depth += 1
        elif c in ')]':
            depth -= 1
            if depth == 0 and start is not None:
                txt = ''.join(buf[start:]) + c
                tok = txt[1:].strip().split(None, 1)[0] if len(txt) > 1 else ''
                forms.append((start_line, tok, ' '.join(txt.split())[:60]))
                start = None
        buf.append(c)
    return forms


def main(path):
    text = open(path, encoding='utf-8', errors='replace').read()
    code = strip_code(text)
    stack = []
    line = 1
    errors = []
    for c in code:
        if c == '\n':
            line += 1
        elif c in '([':
            stack.append((c, line))
        elif c in ')]':
            if not stack:
                errors.append("line %d: unmatched %s" % (line, c))
            else:
                op, ol = stack.pop()
                if (op == '(' and c != ')') or (op == '[' and c != ']'):
                    errors.append("line %d: %s closes %s from line %d" % (line, c, op, ol))
    for op, ol in stack:
        errors.append("line %d: unclosed %s" % (ol, op))
    print("depth=%d errors=%d" % (len(stack), len(errors)))
    for e in errors:
        print("  " + e)
    print("top-level forms:")
    for ln, tok, head in top_forms(code):
        print("  line %d: %s | %s" % (ln, tok, head))
    return 0 if not errors else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1]))
