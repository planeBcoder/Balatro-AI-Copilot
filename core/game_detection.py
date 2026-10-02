"""Bounded Steam discovery, public game validation; no drive-wide scan."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import os
import re
import zipfile
import winreg

SUPPORTED_VERSIONS={'1.0.1o'}
APP_ID='2379780'


def parse_vdf(text):
    tokens=re.findall(r'"((?:\\.|[^"\\])*)"|([{}])',text)
    pos=0
    def parse(nested=False):
        nonlocal pos
        out={}
        while pos<len(tokens):
            raw,brace=tokens[pos];pos+=1
            if brace=='}':
                if not nested:raise ValueError('Unbalanced VDF')
                return out
            if brace:raise ValueError('Expected VDF key')
            key=raw.replace('\\\\','\\').replace('\\"','"')
            if pos>=len(tokens):raise ValueError('Missing VDF value')
            value,brace=tokens[pos];pos+=1
            if brace=='{':out[key]=parse(True)
            elif brace:raise ValueError('Missing VDF value')
            else:out[key]=value.replace('\\\\','\\').replace('\\"','"')
        if nested:raise ValueError('Unbalanced VDF')
        return out
    return parse()


@dataclass(frozen=True)
class GameInstallation:
    path: Path
    version: str
    supported: bool
    reason: str=''


def inspect_game(path):
    path=Path(path).expanduser()
    if path.name.lower()=='balatro.exe':path=path.parent
    path=path.resolve()
    exe=path/'Balatro.exe'
    try:
        with exe.open('rb') as stream:
            if stream.read(2)!=b'MZ':raise ValueError('Not a Windows executable')
        with zipfile.ZipFile(exe) as z:
            entry=z.getinfo('globals.lua')
            if entry.file_size>2_000_000:raise ValueError('Game metadata too large')
            source=z.read(entry).decode('utf-8')
            if not {'main.lua','game.lua','card.lua','conf.lua'}.issubset(z.namelist()):raise ValueError('Incomplete Balatro')
            match=re.search(r"\bVERSION\s*=\s*['\"]([^'\"]+)['\"]",source)
            if not match:raise ValueError('Missing version')
            version=match[1]
            full=bool(re.search(r"VERSION\s*=\s*VERSION\s*\.\.\s*['\"]-FULL['\"]",source))
        if not full:return GameInstallation(path,version,False,'试玩版或非 Steam 游戏包未适配。')
        supported=version in SUPPORTED_VERSIONS
        return GameInstallation(path,version+'-FULL',supported,'' if supported else '此游戏版本未验证，暂不自动安装。')
    except (OSError,ValueError,KeyError,zipfile.BadZipFile,UnicodeError):
        return GameInstallation(path,'未知',False,'未找到完整的 Windows Steam Balatro。')


def steam_roots():
    candidates=[]
    for hive,key,value in [(winreg.HKEY_CURRENT_USER,r'Software\Valve\Steam','SteamPath'),
        (winreg.HKEY_LOCAL_MACHINE,r'SOFTWARE\WOW6432Node\Valve\Steam','InstallPath'),
        (winreg.HKEY_LOCAL_MACHINE,r'SOFTWARE\Valve\Steam','InstallPath')]:
        try:
            with winreg.OpenKey(hive,key) as handle:candidates.append(Path(winreg.QueryValueEx(handle,value)[0]))
        except OSError:pass
    for key in ['PROGRAMFILES(X86)','PROGRAMFILES']:
        if os.environ.get(key):candidates.append(Path(os.environ[key])/'Steam')
    return list(dict.fromkeys(p.resolve() for p in candidates if p.is_dir()))


def discover_games(roots=None,saved_path=None,running_paths=()):
    candidates=[]
    if saved_path:candidates.append(Path(saved_path))
    candidates.extend(Path(p).parent for p in running_paths)
    for root in steam_roots() if roots is None else roots:
        root=Path(root);libraries=[root]
        try:
            vdf=parse_vdf((root/'steamapps/libraryfolders.vdf').read_text(encoding='utf-8-sig'))
            for n,entry in vdf.get('libraryfolders',{}).items():
                if n.isdigit():
                    value=entry.get('path') if isinstance(entry,dict) else entry
                    if value:libraries.append(Path(value))
        except (OSError,ValueError,UnicodeError):pass
        for library in libraries:
            try:
                data=parse_vdf((library/f'steamapps/appmanifest_{APP_ID}.acf').read_text(encoding='utf-8-sig'))['AppState']
                if data.get('appid')!=APP_ID:continue
                folder=data['installdir']
                if not folder or Path(folder).name!=folder or folder in {'.','..'}:continue
                candidates.append(library/'steamapps/common'/folder)
            except (OSError,ValueError,KeyError,UnicodeError):pass
            candidates.append(library/'steamapps/common/Balatro')
    seen=set();found=[]
    for p in candidates:
        p=p.resolve()
        if p in seen or not (p/'Balatro.exe').is_file():continue
        seen.add(p);found.append(inspect_game(p))
    return found


def selected_game():
    from config.settings import SettingsStore
    saved=SettingsStore().load().game_install
    if saved:
        chosen=inspect_game(saved)
        if chosen.supported:return chosen.path
    results=discover_games()
    supported=[g for g in results if g.supported]
    if len(supported)==1:return supported[0].path
    if not supported:raise FileNotFoundError('未找到受支持的 Balatro，请运行安装器选择游戏目录。')
    raise FileNotFoundError('找到多个 Balatro，请在安装器中选择要使用的游戏。')
