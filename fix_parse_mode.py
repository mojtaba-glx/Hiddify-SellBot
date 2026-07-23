#!/usr/bin/env python3
"""Fix parse_mode in AgentBot handlers.py"""
import sys

filepath = sys.argv[1] if len(sys.argv) > 1 else 'AgentBot/handlers.py'

with open(filepath, 'r', encoding='utf-8') as f:
    lines = f.readlines()

result = []
i = 0
fixed = 0

while i < len(lines):
    line = lines[i]
    stripped = line.rstrip()

    is_call = ('.reply_text(' in stripped or '.edit_message_text(' in stripped)
    already_has_pm = 'parse_mode' in stripped

    if not is_call or already_has_pm:
        result.append(line)
        i += 1
        continue

    # Collect all lines of this call by tracking parens
    call_lines = [line]
    depth = 0
    for ch in stripped[stripped.index('('):]:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1

    j = i + 1
    while depth > 0 and j < len(lines):
        call_lines.append(lines[j])
        for ch in lines[j].rstrip():
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
        j += 1

    # Check if the full call already has parse_mode
    full_text = ''.join(call_lines)
    if 'parse_mode' in full_text:
        result.extend(call_lines)
        i = j
        continue

    # Get indentation from first arg line (line after the opening call)
    indent = '            '  # default
    if len(call_lines) > 1:
        for ch in call_lines[1]:
            if ch in ' \t':
                indent += ch if not indent.strip() else ''
            else:
                break
        # Better: copy indent from first arg line
        indent = ''
        for ch in call_lines[1]:
            if ch in ' \t':
                indent += ch
            else:
                break

    last_line_stripped = call_lines[-1].rstrip()

    if last_line_stripped.strip() == ')' and len(call_lines) > 1:
        # Multi-line call with closing paren on its own line
        prev_idx = len(call_lines) - 2
        prev_line = call_lines[prev_idx].rstrip()

        if prev_line.endswith(','):
            # Already has trailing comma - just insert parse_mode before )
            new_line = indent + 'parse_mode="HTML",\n'
            call_lines.insert(-1, new_line)
        else:
            # Add comma to prev line, then add parse_mode
            call_lines[prev_idx] = prev_line + ',\n'
            new_line = indent + 'parse_mode="HTML",\n'
            call_lines.insert(-1, new_line)
    else:
        # Single-line call
        full = stripped
        func_pos = max(full.rfind('.reply_text('), full.rfind('.edit_message_text('))
        paren_start = full.index('(', func_pos)
        d = 0
        close_pos = -1
        for k in range(paren_start, len(full)):
            if full[k] == '(':
                d += 1
            elif full[k] == ')':
                d -= 1
                if d == 0:
                    close_pos = k
                    break
        if close_pos > 0:
            new_full = full[:close_pos] + ', parse_mode="HTML"' + full[close_pos:]
            call_lines[0] = new_full + '\n'

    result.extend(call_lines)
    fixed += 1
    i = j

with open(filepath, 'w', encoding='utf-8') as f:
    f.writelines(result)

print(f'Fixed {fixed} calls')
