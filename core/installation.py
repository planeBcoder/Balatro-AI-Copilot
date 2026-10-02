"""Explicit, bounded game integration. Never opens game saves or patches the EXE.

Owned files have durable preimages and hash-checked rollback/uninstall. Shared
loaders are preserved on uninstall, since other mods may subsequently use them.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import uuid
import zipfile
from functools import wraps

from core.game_detection import inspect_game
from core.hud_install import precision_exporter_text

MOD_IDS = {'BalatroStateExporter':'balatro_state_exporter',
           'BalatroCopilotHUD':'balatro_copilot_hud',
           'BalatroCopilotAutoPlay':'balatro_copilot_autoplay'}


def serialized(method):
    @wraps(method)
    def guarded(*args,**kwargs):
        from core.single_instance import SingleInstance
        guard=SingleInstance('BalatroAICopilotIntegration')
        try:
            if guard.existing:raise OSError('另一个安装或卸载操作正在进行，请稍后重试。')
            return method(*args,**kwargs)
        finally:guard.close()
    return guarded


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, data):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.copilot-'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('xb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)


def bounded(root, relative):
    """Reject traversal and junction/symlink redirection, including root itself."""
    root=Path(root).absolute()
    part=PurePosixPath(relative)
    if not relative or '\\' in relative or part.is_absolute() or any(p in {'..','.'} or ':' in p for p in part.parts):
        raise OSError('安装记录包含不安全路径。')
    path=root.joinpath(*part.parts)
    for parent in (path,*path.parents):
        if parent.is_symlink() or (hasattr(parent,'is_junction') and parent.is_junction()):
            raise OSError('检测到符号链接或目录联接，拒绝写入。')
    if not path.resolve().is_relative_to(root.resolve()):raise OSError('安装路径越界。')
    return path


def verified_archive(dependencies, name):
    lock=json.loads((Path(dependencies)/'dependencies.lock.json').read_text(encoding='utf-8'))[name]
    path=Path(dependencies)/lock['filename']
    data=path.read_bytes()
    if digest(data)!=lock['sha256']:raise OSError('适配组件校验失败，请重新下载官方安装包。')
    return path,lock


def archive_files(path,prefix=''):
    files={}
    with zipfile.ZipFile(path) as archive:
        total=0
        for entry in archive.infolist():
            if entry.is_dir():continue
            total+=entry.file_size
            if total>150_000_000 or entry.file_size>30_000_000:raise OSError('组件体积异常。')
            if prefix and not entry.filename.startswith(prefix):raise OSError('组件目录异常。')
            name=entry.filename[len(prefix):]
            part=PurePosixPath(name)
            if not name or '\\' in name or part.is_absolute() or any(p in {'..','.'} or ':' in p for p in part.parts):
                raise OSError('组件压缩包包含不安全路径。')
            if (entry.external_attr>>16)&0o170000==0o120000:raise OSError('组件包含符号链接。')
            if name in files:raise OSError('组件包含重复文件。')
            files[name]=archive.read(entry)
    return files


def exporter_text(assets):
    assets=Path(assets)
    src=(assets/'exporter/main.lua').read_text(encoding='utf-8')+'\n'+(assets/'exporter_extension.lua').read_text(encoding='utf-8')
    anchor='        state.request_id = token'
    if src.count(anchor)!=1:raise OSError('状态接口模板异常。')
    for name in ('scoring_snapshot.lua','autoplay_snapshot.lua','shop_snapshot.lua'):
        src=src.replace(anchor,anchor+'\n'+(assets/name).read_text(encoding='utf-8'),1)
    score='and id ~= "balatro_state_exporter" and id ~= "balatro_copilot_hud"'
    src=src.replace(score,score+' and id ~= "balatro_copilot_autoplay"',1)
    return precision_exporter_text(src)


class Integration:
    def __init__(self,game,profile,root,assets,dependencies,running=lambda:False):
        self.game=Path(game).absolute();self.profile=Path(profile).absolute()
        self.root=Path(root).absolute();self.assets=Path(assets);self.dependencies=Path(dependencies)
        self.running=running
        self.ledger=self.root/'integration.json';self.journal=self.root/'integration-pending.json'

    def target(self,entry):
        kind,rel=entry['root'],entry['path']
        if kind=='game' and rel=='winmm.dll':return bounded(self.game,rel)
        if kind=='profile':
            parts=PurePosixPath(rel).parts
            if len(parts)>=3 and parts[0]=='Mods' and parts[1] in {*MOD_IDS,'Steamodded'}:
                return bounded(self.profile,rel)
        raise OSError('安装记录指向非副驾驶文件。')

    def backup(self,name):
        if not re.fullmatch(r'[a-f0-9]{32}/[0-9]+\.bin',name):raise OSError('备份路径异常。')
        return bounded(self.root,'integration-backups/'+name)

    def read_ledger(self):
        if not self.ledger.exists():return {'game':str(self.game),'profile':str(self.profile),'files':[]}
        data=json.loads(self.ledger.read_text(encoding='utf-8'))
        if data.get('game')!=str(self.game) or data.get('profile')!=str(self.profile):
            raise OSError('已适配另一份游戏。请先卸载旧的适配组件再更换目录。')
        for entry in data['files']:self.target(entry)
        return data

    def recover(self):
        if not self.journal.exists():return
        pending=json.loads(self.journal.read_text(encoding='utf-8'))
        # A completed ledger with this transaction proves the commit succeeded.
        if self.ledger.exists() and json.loads(self.ledger.read_text(encoding='utf-8')).get('transaction')==pending['transaction']:
            self.journal.unlink();return
        conflicts=[]
        for entry in reversed(pending['files']):
            path=self.target(entry)
            current=digest(path.read_bytes()) if path.exists() else None
            if current==entry['before_hash']:continue
            if current!=entry['installed_hash']:
                conflicts.append(str(path));continue
            if entry['before']:
                data=self.backup(entry['before']).read_bytes()
                if digest(data)!=entry['before_hash']:raise OSError('回滚备份校验失败。')
                atomic(path,data)
            else:path.unlink(missing_ok=True)
        if conflicts:raise OSError('中断后文件被外部修改，已保留，请人工检查：'+', '.join(conflicts))
        self.journal.unlink()

    def plan(self):
        inspected=inspect_game(self.game)
        if not inspected.supported:raise OSError(inspected.reason)
        lovely,lock=verified_archive(self.dependencies,'lovely')
        lovely_files=archive_files(lovely)
        dll=lovely_files['winmm.dll']
        smods,sm_lock=verified_archive(self.dependencies,'steamodded')
        sm_files=archive_files(smods,'smods-'+sm_lock['version']+'/')
        writes=[]
        def add(kind,name,data,shared=False):
            entry={'root':kind,'path':name,'shared':shared,'installed_hash':digest(data)}
            self.target(entry)
            writes.append((entry,data))
        old=bounded(self.game,'version.dll')
        if old.exists():raise OSError('存在旧版 Lovely 或其他 version.dll，请先在原模组工具中处理；副驾驶不会覆盖它。')
        winmm=bounded(self.game,'winmm.dll')
        if winmm.exists() and digest(winmm.read_bytes())!=digest(dll):
            raise OSError('已有不同版本的 winmm.dll，已保留。当前公测要求 Lovely 0.10.0。')
        if not winmm.exists():add('game','winmm.dll',dll,True)
        mods=bounded(self.profile,'Mods')
        if mods.exists():
            for folder in mods.iterdir():
                if not folder.is_dir() or folder.name=='Steamodded':continue
                # Steamodded can be installed under an arbitrary folder name.
                if (folder/'core/core.lua').exists() or (folder/'steamodded.lua').exists():
                    raise OSError('发现非标准目录的 Steamodded，请统一安装到 Mods/Steamodded 后重试。')
        sm=bounded(self.profile,'Mods/Steamodded')
        if sm.exists():
            version=bounded(self.profile,'Mods/Steamodded/version.lua')
            if not version.exists() or not re.search(r'"'+re.escape(sm_lock['version'])+r'"',version.read_text(encoding='utf-8')):
                raise OSError('已有不同版本的 Steamodded，已保留。当前公测要求 '+sm_lock['version']+'。')
            # Validate required engine files, not just a misleading version string.
            for name in ('lovely.toml','core/core.lua','version.lua'):
                if name in sm_files:
                    installed=bounded(self.profile,'Mods/Steamodded/'+name)
                    if not installed.exists() or digest(installed.read_bytes())!=digest(sm_files[name]):
                        raise OSError('已有 Steamodded 的核心文件与已验证版本不符，拒绝覆盖。')
        else:
            for name,data in sm_files.items():add('profile','Mods/Steamodded/'+name,data,True)
        for folder,mod_id in MOD_IDS.items():
            target=bounded(self.profile,'Mods/'+folder)
            if target.exists():
                manifest=bounded(self.profile,'Mods/'+folder+'/manifest.json')
                try:owned=json.loads(manifest.read_text(encoding='utf-8')).get('id')==mod_id
                except (OSError,ValueError):owned=False
                if not owned:raise OSError('已有同名但来源未知的模组：'+folder+'，已保留。')
            asset_folder={'BalatroStateExporter':'exporter','BalatroCopilotHUD':'game_hud','BalatroCopilotAutoPlay':'autoplay'}[folder]
            for name in ('main.lua','manifest.json'):
                data=exporter_text(self.assets).encode('utf-8') if folder=='BalatroStateExporter' and name=='main.lua' else (self.assets/asset_folder/name).read_bytes()
                add('profile','Mods/'+folder+'/'+name,data)
        return writes

    @serialized
    def install(self):
        if self.running():raise OSError('请先退出 Balatro，再点击安装或修复。不会强制关闭游戏。')
        self.recover()
        old=self.read_ledger()
        writes=self.plan()  # Complete validation before the first game write.
        entries={(e['root'],e['path']):dict(e) for e in old['files']}
        transaction=uuid.uuid4().hex;pending=[];changes=[]
        for n,(entry,data) in enumerate(writes):
            path=self.target(entry)
            before=path.read_bytes() if path.exists() else None
            if before==data:continue
            ident=(entry['root'],entry['path'])
            tracked=entries.get(ident)
            if tracked and digest(before or b'')!=tracked['installed_hash']:
                raise OSError('已安装文件被其他程序修改，拒绝覆盖：'+entry['path'])
            backup_name=f'{transaction}/{n}.bin' if before is not None else None
            if backup_name:atomic(self.backup(backup_name),before)
            pending.append({**entry,'before':backup_name,'before_hash':digest(before) if before is not None else None})
            entries[ident]={**entry,'original':tracked['original'] if tracked else backup_name,
                            'original_hash':tracked['original_hash'] if tracked else (digest(before) if before is not None else None)}
            changes.append((entry,data))
        if not changes:return {'changed':0,'shared_preserved':True}
        atomic(self.journal,json.dumps({'transaction':transaction,'files':pending}).encode('utf-8'))
        try:
            for entry,data in changes:atomic(self.target(entry),data)
            atomic(self.ledger,json.dumps({'schema':1,'transaction':transaction,'game':str(self.game),'profile':str(self.profile),'files':list(entries.values())},ensure_ascii=False,indent=2).encode('utf-8'))
        except BaseException:
            self.recover();raise
        self.journal.unlink(missing_ok=True)
        return {'changed':len(changes),'shared_preserved':True}

    @serialized
    def uninstall(self):
        if not self.ledger.exists() and not self.journal.exists():return {'removed':0,'preserved':[]}
        if self.running():raise OSError('请先退出 Balatro 再卸载，避免移除正在使用的组件。')
        self.recover();data=self.read_ledger();preserved=[];remaining=[];removed=0
        for entry in reversed(data['files']):
            if entry.get('shared'):continue  # Keep shared loaders for other mods.
            path=self.target(entry)
            current=digest(path.read_bytes()) if path.exists() else None
            # Resume an interrupted uninstall without overwriting new user edits.
            if current==entry.get('original_hash'):continue
            if current!=entry['installed_hash']:
                preserved.append(entry['path']);remaining.append(entry);continue
            if entry.get('original'):
                original=self.backup(entry['original']).read_bytes()
                if digest(original)!=entry['original_hash']:raise OSError('原始组件备份校验失败。')
                atomic(path,original)
            else:path.unlink(missing_ok=True)
            removed+=1
        for folder in MOD_IDS:
            path=bounded(self.profile,'Mods/'+folder)
            if path.exists() and not any(path.iterdir()):path.rmdir()
        if remaining:
            data['files']=remaining
            atomic(self.ledger,json.dumps(data,ensure_ascii=False,indent=2).encode('utf-8'))
        else:self.ledger.unlink(missing_ok=True)
        return {'removed':removed,'preserved':preserved}


def default_integration(game):
    from config.settings import APP_DIR,GAME_DIR,resource
    from core.windows import balatro_pids
    return Integration(game,GAME_DIR,APP_DIR,resource('assets'),resource('dependencies'),lambda:bool(balatro_pids()))


def uninstall_default():
    from config.settings import APP_DIR
    path=APP_DIR/'integration.json'
    pending=APP_DIR/'integration-pending.json'
    if not path.exists() and not pending.exists():return {'removed':0,'preserved':[]}
    from config.settings import SettingsStore
    game=SettingsStore().load().game_install
    if not game:raise OSError('安装位置记录缺失，已保留游戏组件。')
    return default_integration(game).uninstall()
