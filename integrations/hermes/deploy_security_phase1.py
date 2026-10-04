"""Back up and apply the canonical Phase 1 integration. Never restart the gateway."""
from datetime import datetime, timezone
from pathlib import Path
import ast
import copy
import hashlib
import json
import shutil
import sys
import yaml

ROOT=Path(__file__).resolve().parents[2]
HOME=Path("C:/Users/win-10/AppData/Local/hermes")
CORE=HOME/"hermes-agent"
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    report=Path((ROOT/"runtime/phase1-active.txt").read_text())
    manifest=json.loads((report/"manifest.json").read_text())
    def backup(p):
        if not p.exists(): return
        if any(x["path"]==str(p) for x in manifest): return
        target=report/"backups"/str(len(manifest))/p.name
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
        assert digest(p)==digest(target)
        manifest.append({"path":str(p),"backup":str(target),"before_sha256":digest(p)})
        (report/"manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    pairs=[]
    source=ROOT/"integrations/hermes/plugins/komatso-public-research"
    for p in source.iterdir():
        if p.is_file() and p.suffix in {".py",".yaml",".cjs"}:
            if p.suffix==".py": ast.parse(p.read_text(encoding="utf-8"),filename=str(p))
            pairs.append((p,HOME/"plugins/komatso-public-research"/p.name))
            pairs.append((p,HOME/"profiles/maintenance/plugins/komatso-public-research"/p.name))
    provider=ROOT/"integrations/hermes/plugins/komatso-parallel/provider.py"
    pairs.append((provider,HOME/"plugins/komatso-parallel/provider.py"))
    twin=HOME/"profiles/maintenance/plugins/komatso-parallel/provider.py"
    if twin.exists(): pairs.append((provider,twin))
    configs=[]
    for profile,home in [("default",HOME),("maintenance",HOME/"profiles/maintenance")]:
        path=home/"config.yaml";backup(path)
        before=yaml.safe_load(path.read_text(encoding="utf-8"))
        cfg=copy.deepcopy(before)
        selected=["bale","telegram"] if profile=="default" else ["bale"]
        for platform in selected:
            toolsets=["web","skills_readonly","komatso_public_browser"]
            if profile=="maintenance": toolsets+=["delegation","komatso_maintenance"]
            cfg["platform_toolsets"][platform]=toolsets+["no_mcp"]
        enabled=cfg.setdefault("plugins",{}).setdefault("enabled",[])
        if "komatso-public-research" not in enabled: enabled.append("komatso-public-research")
        known=cfg.setdefault("known_plugin_toolsets",{})
        for platform in cfg["platform_toolsets"]:
            values=known.setdefault(platform,[])
            if platform in selected:
                if "komatso_public_browser" in values: values.remove("komatso_public_browser")
            elif "komatso_public_browser" not in values:
                values.append("komatso_public_browser")
        cfg.setdefault("skills",{})["inline_shell"]=False
        # Routes, transport credentials, and all business authorization are preserved exactly.
        for key in before:
            if key not in {"platform_toolsets","known_plugin_toolsets","plugins","skills"}:
                assert cfg[key]==before[key],key
        configs.append((path,yaml.safe_dump(cfg,allow_unicode=True,sort_keys=False)))
    # All validations/backups precede the first runtime write.
    for source,target in pairs: backup(target)
    twins=[]
    for source,target in pairs:
        target.parent.mkdir(parents=True,exist_ok=True)
        old=digest(target) if target.exists() else None
        if not target.exists() or digest(source)!=digest(target):
            shutil.copy2(source,target)
        assert digest(source)==digest(target)
        twins.append({"canonical":str(source),"runtime":str(target),"before_runtime_sha256":old,"after_sha256":digest(target)})
    for path,text in configs:
        if path.read_text(encoding="utf-8")==text:
            continue
        temp=path.with_name(path.name+".phase1-new")
        temp.write_text(text,encoding="utf-8")
        assert isinstance(yaml.safe_load(temp.read_text(encoding="utf-8")),dict)
        temp.replace(path)
    previous=json.loads((report/"runtime-deploy.json").read_text()) if (report/"runtime-deploy.json").exists() else []
    old_twins={record["runtime"]:record for record in previous}
    for record in twins:
        if record["runtime"] in old_twins:
            record["before_runtime_sha256"]=old_twins[record["runtime"]]["before_runtime_sha256"]
    (report/"runtime-deploy.json").write_text(json.dumps(twins,indent=2),encoding="utf-8")
    print("Applied canonical plugins and runtime-only configs; no gateway restart.")
    print(report)
if __name__=="__main__":main()
