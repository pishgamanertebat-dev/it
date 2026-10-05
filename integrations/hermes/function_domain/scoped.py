"""Scoped business-data I/O. Fixed root, read-only OS handles, no code execution."""
from __future__ import annotations
from contextlib import contextmanager, ExitStack
import ctypes
from ctypes import wintypes
import io, os, re, stat
from pathlib import Path, PureWindowsPath

FUNCTION_ROOT = Path(r'E:\Function')
MAX_PARSE_BYTES = 64 * 1024 * 1024
MAX_ATTACH_BYTES = 45 * 1024 * 1024

class ScopeDenied(PermissionError):
    pass

class ScopedReader:
    def __init__(self, root=FUNCTION_ROOT, *, allowed_files=None):
        # Injectable only by trusted Python callers for fixtures, never the tool schema.
        self.root = Path(root).absolute()
        self.allowed_files = None if allowed_files is None else frozenset(allowed_files)
        if self.allowed_files is not None:
            for name in self.allowed_files:
                if self.relative(name) != [name]:
                    raise ScopeDenied('Exact-file scope requires root filenames')

    def restricted(self, files):
        files = frozenset(files)
        if self.allowed_files is not None:
            files &= self.allowed_files
        return ScopedReader(self.root, allowed_files=files)

    def check_file_scope(self, parts, *, directory=False):
        if self.allowed_files is not None and (directory or '/'.join(parts) not in self.allowed_files):
            raise ScopeDenied('Resource is outside the identity file scope')

    def relative(self, value=''):
        if not isinstance(value,str) or len(value)>2048 or any(ord(c)<32 for c in value):
            raise ScopeDenied('Invalid relative path')
        value=value.replace('\\','/')
        win=PureWindowsPath(value)
        if win.drive or win.root or value.startswith('/') or ':' in value or '%' in value:
            raise ScopeDenied('Only relative paths under the business root are accepted')
        parts=value.split('/') if value else []
        if any(p in {'..','.'} or not p or p.endswith((' ','.')) or
               re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',p,re.I)
               or any(c in p for c in '*?"<>|') for p in parts):
            raise ScopeDenied('Unsafe path component')
        return parts

    def contained(self,path):
        try: Path(path).relative_to(self.root)
        except ValueError: raise ScopeDenied('Canonical path outside the business root') from None

    @contextmanager
    def _handle(self,path,directory):
        if os.name!='nt':
            info=os.lstat(path)
            if stat.S_ISLNK(info.st_mode):raise ScopeDenied('Links are unavailable')
            resolved=path.resolve(strict=True);self.contained(resolved)
            if directory:
                yield None
            else:
                with path.open('rb') as f:yield f
            return
        import msvcrt
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        create=kernel.CreateFileW
        create.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        create.restype=wintypes.HANDLE
        close=kernel.CloseHandle;close.argtypes=[wintypes.HANDLE];close.restype=wintypes.BOOL
        final=kernel.GetFinalPathNameByHandleW
        final.argtypes=[wintypes.HANDLE,wintypes.LPWSTR,wintypes.DWORD,wintypes.DWORD];final.restype=wintypes.DWORD
        class Tag(ctypes.Structure):_fields_=[('attributes',wintypes.DWORD),('tag',wintypes.DWORD)]
        tag_info=kernel.GetFileInformationByHandleEx
        tag_info.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD];tag_info.restype=wintypes.BOOL
        # Parents share READ/WRITE but never DELETE: no rename/junction substitution.
        # Files share READ only: an active writer cannot change a parsed snapshot.
        handle=create(str(path),0x80 if directory else 0x80000000,3 if directory else 1,
                      None,3,0x00200000 | (0x02000000 if directory else 0),None)
        if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
        owned=True
        try:
            tag=Tag()
            if not tag_info(handle,9,ctypes.byref(tag),ctypes.sizeof(tag)):raise ctypes.WinError(ctypes.get_last_error())
            if tag.attributes & 0x400:raise ScopeDenied('Reparse points are unavailable')
            if bool(tag.attributes & 0x10)!=directory:raise ScopeDenied('Unexpected resource type')
            buf=ctypes.create_unicode_buffer(32768)
            size=final(handle,buf,len(buf),0)
            if not size or size>=len(buf):raise ScopeDenied('Cannot verify final handle path')
            resolved=buf.value
            if resolved.startswith('\\\\?\\UNC\\'):raise ScopeDenied('UNC resources are unavailable')
            if resolved.startswith('\\\\?\\'):resolved=resolved[4:]
            self.contained(Path(resolved))
            if directory:yield None
            else:
                fd=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY);owned=False
                with os.fdopen(fd,'rb') as f:yield f
        finally:
            if owned:close(handle)

    @contextmanager
    def open(self,relative='',*,directory=False):
        parts=self.relative(relative)
        self.check_file_scope(parts, directory=directory)
        if not parts and not directory:raise ScopeDenied('A file path is required')
        with ExitStack() as stack:
            stack.enter_context(self._handle(self.root,True))
            target=self.root
            for index,part in enumerate(parts):
                target=target/part
                resource=stack.enter_context(self._handle(target,directory or index<len(parts)-1))
            # Verify both canonical spelling and actual OS handle after all parents are pinned.
            self.contained(target.resolve(strict=True))
            yield target,None if directory else resource

    def metadata(self,relative):
        parts=self.relative(relative)
        self.check_file_scope(parts)
        target=self.root.joinpath(*parts)
        with self.open(relative,directory=target.is_dir()) as (p,f):
            info=p.stat() if f is None else os.fstat(f.fileno())
            return {'path':'/'.join(parts),'directory':stat.S_ISDIR(info.st_mode),
                    'size':info.st_size,'modified_ns':info.st_mtime_ns,'format':p.suffix.lower()}

    def list(self,relative='',*,recursive=False,pattern='',offset=0,limit=200):
        if not isinstance(pattern,str) or len(pattern)>256:raise ValueError('Invalid filename search')
        if type(offset)!=int or offset<0 or type(limit)!=int or not 1<=limit<=500:raise ValueError('Invalid page')
        if self.allowed_files is not None:
            # Enumerate only fixed authorized names: no out-of-scope scandir or skipped-path leaks.
            if self.relative(relative):
                raise ScopeDenied('Directories are outside the identity file scope')
            entries=[]
            for name in sorted(self.allowed_files, key=str.casefold):
                if pattern and pattern.casefold() not in name.casefold():continue
                try:entries.append(self.metadata(name))
                except (ScopeDenied,OSError):continue
            total=len(entries);page=entries[offset:offset+limit]
            return {'entries':page,'total':total,'offset':offset,
                    'next_offset':offset+len(page) if offset+len(page)<total else None,
                    'skipped_unreadable_or_links':[]}
        entries=[];skipped=[];total=0
        def visit(rel):
            nonlocal total
            with self.open(rel,directory=True) as (p,_):
                for item in sorted(os.scandir(p),key=lambda x:x.name.casefold()):
                    child='/'.join(filter(None,[rel,item.name]))
                    info=item.stat(follow_symlinks=False)
                    if item.is_symlink() or getattr(info,'st_file_attributes',0)&0x400:
                        skipped.append(child);continue
                    try:
                        meta=self.metadata(child)
                        if not pattern or pattern.casefold() in item.name.casefold():
                            if offset<=total<offset+limit:entries.append(meta)
                            total+=1
                        if recursive and meta['directory']:visit(child)
                    except (ScopeDenied,OSError):skipped.append(child)
        visit(relative)
        return {'entries':entries,'total':total,'offset':offset,
                'next_offset':offset+len(entries) if offset+len(entries)<total else None,
                'skipped_unreadable_or_links':skipped}

    def snapshot(self,relative,*,maximum=MAX_PARSE_BYTES):
        with self.open(relative) as (_,f):
            size=os.fstat(f.fileno()).st_size
            if size>maximum:raise ValueError('File exceeds this operation limit; use paged reads or attachment')
            data=f.read(maximum+1)
            if len(data)>maximum:raise ValueError('File exceeds operation limit')
            return data
