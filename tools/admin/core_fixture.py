"""Apply the reviewed generic patch to in-memory native source for hermetic tests.

Works before and after activation, without editing the installed Hermes checkout.
"""
from pathlib import Path
import re
ROOT=Path(__file__).resolve().parents[2]
CORE=Path('C:/Users/win-10/AppData/Local/hermes/hermes-agent')
PATCH=ROOT/'integrations/hermes/patches/admin1-role-routing-media.patch'

def patched_core_source(relative):
    patch=PATCH.read_text(encoding='utf-8').splitlines(True)
    marker='--- a/'+relative+'\n'
    start=patch.index(marker)+2
    end=next((i for i in range(start,len(patch)) if patch[i].startswith('--- a/')),len(patch))
    hunks=patch[start:end]
    source=(CORE/relative).read_text(encoding='utf-8')
    def apply(reverse=False):
        original=source.splitlines(True);out=[];cursor=0;index=0
        while index<len(hunks):
            line=hunks[index]
            match=re.match(r'@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@',line)
            if not match:raise ValueError('Malformed native patch hunk')
            position=int(match.group(2 if reverse else 1))-1
            out.extend(original[cursor:position]);cursor=position;index+=1
            while index<len(hunks) and not hunks[index].startswith('@@ '):
                line=hunks[index];index+=1
                if line.startswith('\\'):continue
                sign=line[0];text=line[1:]
                if reverse:sign='-' if sign=='+' else '+' if sign=='-' else sign
                if sign in {' ','-'}:
                    if cursor>=len(original) or original[cursor]!=text:raise ValueError('Native patch preimage changed; review required')
                    cursor+=1
                if sign in {' ','+'}:out.append(text)
        out.extend(original[cursor:]);return ''.join(out)
    try:return apply()
    except ValueError:
        apply(reverse=True) # Verify that the reviewed patch is already present.
        return source
