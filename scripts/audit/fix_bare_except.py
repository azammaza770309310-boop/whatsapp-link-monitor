#!/usr/bin/env python3
"""
Fix bare `except:` clauses by replacing them with `except Exception:`.
This preserves KeyboardInterrupt/SystemExit/asyncio.CancelledError propagation.

Operates line-by-line to ensure precision. Only matches the pattern
`except:` (with optional whitespace) at the start of an except clause.
"""
import re
import sys
from pathlib import Path

# Matches: except:  (with optional leading whitespace)
# Does NOT match: except SomeError:  or  except (E1, E2):
BARE_EXCEPT_RE = re.compile(r'^(\s*)except\s*:\s*(.*)$')

def fix_file(path: Path) -> int:
    """Returns number of substitutions made."""
    text = path.read_text(encoding='utf-8')
    lines = text.splitlines(keepends=True)
    changes = 0
    new_lines = []
    for line in lines:
        m = BARE_EXCEPT_RE.match(line.rstrip('\n'))
        if m:
            indent, tail = m.group(1), m.group(2)
            # Preserve trailing comment if any, and trailing newline
            newline = '\n' if line.endswith('\n') else ''
            new_line = f"{indent}except Exception:  # noqa: BLE001 - broad catch is intentional here\n" if not tail else f"{indent}except Exception: {tail}\n"
            # Simpler: just replace and preserve rest of line
            new_line = f"{indent}except Exception:{' ' + tail if tail else ''}{newline}"
            new_lines.append(new_line)
            changes += 1
        else:
            new_lines.append(line)
    if changes:
        path.write_text(''.join(new_lines), encoding='utf-8')
    return changes

if __name__ == '__main__':
    total = 0
    for f in sys.argv[1:]:
        p = Path(f)
        if not p.exists():
            print(f"SKIP (missing): {f}")
            continue
        n = fix_file(p)
        print(f"  {f}: {n} bare-except fixed")
        total += n
    print(f"TOTAL: {total} fixes applied")
