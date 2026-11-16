#!/usr/bin/env python3
"""
cockpit_sync.py — 本机 ↔ Cloudflare 任务验收台 的同步

老板要「像批奏折一样，在手机上清任务」。这个脚本两头跑：

  Mac → 云端：读本机看板，推待验收/待确认的卡到网页
  云端 → Mac：把老板在网页上点的「通过/打回」拉回来，改本机看板

为什么用「拉」而不是「网页直接改本机」：
  本机没有公网 IP，网页够不着。让 Mac 主动去拉，就不需要开端口、不需要内网穿透。
  代价是点完到生效有几分钟延迟 —— 批奏折本来就是有空才清，能接受。

用法：
    python3 cockpit_sync.py                 跑一次（推 + 拉）
    python3 cockpit_sync.py --push-only     只推数据
    python3 cockpit_sync.py --pull-only     只拉动作
    python3 cockpit_sync.py --dry-run       只看会做什么，不真改

配置从同目录 config.json 读（不进 git）：
    {"base_url": "https://xxx.pages.dev", "sync_token": "...", "board_port": 49854}

依赖：只用 Python 标准库。HTTP 用 urllib，不装任何包。
"""

import os
import sys
import json
import ssl
import time
import urllib.request
import urllib.error
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, 'config.json')
BOARD_FILE = os.path.expanduser('~/.dsh/dsh-taskboard.json')
STATE_FILE = os.path.expanduser('~/.dsh/dsh-cockpit-state.json')

# Python 3.13 在这台机器上证书链有问题，curl 能过。
# 所以用 curl 走 HTTP，避免 SSL 报错。
USE_CURL = True


def find_port(cfg):
    """DSH 每次重启端口都会变。扫所有本地监听端口，挨个试哪个能读看板。"""
    cands = []
    if cfg.get('board_port'):
        cands.append(cfg['board_port'])

    # 用 netstat 拿所有本地监听端口（比 lsof 稳，不依赖进程名）
    import subprocess
    try:
        out = subprocess.run(['netstat', '-an'], capture_output=True, text=True, timeout=10).stdout
        for line in out.split('\n'):
            if '127.0.0.1.' in line and 'LISTEN' in line:
                try:
                    part = line.split('127.0.0.1.')[1].split()[0]
                    port = int(part)
                    if port not in cands and 1024 < port < 65536:
                        cands.append(port)
                except Exception:
                    pass
    except Exception:
        pass

    # 按端口号从大到小试（DSH 的端口通常在高位段，新的更新）
    for port in sorted(cands, reverse=True):
        try:
            status, text = http('GET', f'http://127.0.0.1:{port}/dsh-taskboard/state', {}, timeout=2)
            if status == 200 and 'dsh-taskboard' in text or (status == 200 and '"ok":true' in text.replace(' ', '')):
                return port
        except Exception:
            continue
    return cfg.get('board_port')


def load_config():
    if not os.path.exists(CONFIG):
        print(f'缺少配置：{CONFIG}', file=sys.stderr)
        sys.exit(2)
    return json.load(open(CONFIG, encoding='utf-8'))


def http(method, url, headers, body=None, timeout=30):
    """用 curl 发请求，返回 (status, 文本)。"""
    import subprocess
    cmd = ['curl', '-s', '--max-time', str(timeout), '-X', method,
           '-w', '\n__STATUS__%{http_code}', url]
    for k, v in headers.items():
        cmd += ['-H', f'{k}: {v}']
    if body is not None:
        cmd += ['-H', 'Content-Type: application/json', '-d', body]
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = r.stdout
    if '__STATUS__' in out:
        text, status = out.rsplit('\n__STATUS__', 1)
        try:
            return int(status.strip()), text
        except ValueError:
            return 0, text
    return 0, out


def read_board():
    """读本机看板文件（只读）。也尝试从 DSH HTTP 接口读，拿到最新的。"""
    cfg = load_config()
    port = find_port(cfg)
    if port:
        try:
            status, text = http('GET', f'http://127.0.0.1:{port}/dsh-taskboard/state', {}, timeout=8)
            if status == 200:
                d = json.loads(text)
                if d.get('ok') and d.get('value', {}).get('tasks') is not None:
                    return d['value']
        except Exception:
            pass
    # 兜底：直接读文件
    try:
        return json.load(open(BOARD_FILE, encoding='utf-8'))
    except Exception as e:
        print(f'读不了看板：{e}', file=sys.stderr)
        sys.exit(1)


def snapshot(board):
    """精简：只留网页要显示的字段，别把整个台账搬上云。"""
    tasks = []
    for t in (board.get('tasks') or []):
        if t.get('trashedAt'):
            continue
        if t.get('status') not in ('in_review', 'in_progress', 'todo', 'backlog'):
            continue
        tasks.append({
            'id': t.get('id'),
            'title': t.get('title'),
            'description': t.get('description'),
            'status': t.get('status'),
            'urgency': t.get('urgency'),
            'version': t.get('version'),
            'blocked': bool(t.get('blocked')),
            'waitingForHuman': bool(t.get('waitingForHuman')),
            'waitingReason': t.get('waitingReason') or '',
            'comments': [
                {'body': c.get('body'), 'createdAt': c.get('createdAt')}
                for c in (t.get('comments') or [])
            ][-5:],
        })
    return {
        'synced_at': int(time.time() * 1000),
        'tasks': tasks,
    }


PUSH_HASH = os.path.expanduser('~/.dsh/cockpit/.last-push')


def push(cfg, dry):
    """数据没变就不推，省 KV 写入额度（免费额度每天只有 1000 次写）。"""
    import hashlib
    snap = snapshot(read_board())
    url = cfg['base_url'].rstrip('/') + '/api/board'
    blob = json.dumps(snap, ensure_ascii=False, sort_keys=True)
    # 指纹只算任务内容，不含 synced_at —— 那个时间戳每次都变，会让去重永远失效
    digest = hashlib.sha256(
        json.dumps(snap['tasks'], ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()

    if not dry:
        try:
            if open(PUSH_HASH).read().strip() == digest:
                print('[推] 没变化，跳过')
                return 0
        except Exception:
            pass

    if dry:
        print(f'[推] {len(snap["tasks"])} 张卡 → {url}')
        return 0
    status, text = http('POST', url, {'X-Sync-Token': cfg['sync_token']},
                        blob, timeout=30)
    if status == 200:
        try:
            open(PUSH_HASH, 'w').write(digest)
        except Exception:
            pass
        print(f'[推] 成功，{len(snap["tasks"])} 张卡')
        return 0
    print(f'[推] 失败 HTTP {status}: {text[:200]}', file=sys.stderr)
    return 1


def move_local(task_id, status, port):
    """通过 DSH 的 GUI 通道移卡。这条通道允许到 done。"""
    # 先拿当前版本号
    st, text = http('GET', f'http://127.0.0.1:{port}/dsh-taskboard/state', {}, timeout=8)
    if st != 200:
        return False, f'读看板失败 HTTP {st}'
    try:
        tasks = json.loads(text)['value']['tasks']
        t = next((x for x in tasks if x['id'] == task_id), None)
    except Exception as e:
        return False, f'解析失败 {e}'
    if not t:
        return False, '卡不存在'
    ver = t['version']
    action = 'move' if status == 'done' else 'reject'
    body = {'ifVersion': ver, 'status': status}
    if status != 'done':
        body['body'] = f'老板在手机上打回（{datetime.datetime.now():%Y-%m-%d %H:%M}）'
    st2, text2 = http('POST',
                      f'http://127.0.0.1:{port}/dsh-taskboard/tasks/{task_id}/{action}',
                      {}, json.dumps(body, ensure_ascii=False), timeout=15)
    try:
        d = json.loads(text2)
        if d.get('ok'):
            return True, f'已移到 {status}'
        return False, d.get('error', {}).get('message', text2[:100])
    except Exception:
        return st2 == 200, text2[:100]


def pull(cfg, dry):
    url = cfg['base_url'].rstrip('/') + '/api/action'
    status, text = http('GET', url, {'X-Sync-Token': cfg['sync_token']}, timeout=30)
    if status != 200:
        print(f'[拉] 失败 HTTP {status}: {text[:200]}', file=sys.stderr)
        return 1
    try:
        actions = json.loads(text).get('actions', [])
    except Exception:
        print(f'[拉] 解析失败: {text[:200]}', file=sys.stderr)
        return 1
    if not actions:
        print('[拉] 没有待处理动作')
        return 0

    seen = set()
    if os.path.exists(STATE_FILE):
        try:
            seen = set(json.load(open(STATE_FILE, encoding='utf-8')).get('seen', []))
        except Exception:
            pass

    port = find_port(cfg)
    ok = fail = skip = 0
    for a in actions:
        key = a.get('key', '')
        if key in seen:
            skip += 1
            continue
        target = 'done' if a.get('action') == 'approve' else 'todo'
        if dry:
            print(f'[拉] 会处理 {a.get("taskId")} → {target}')
            continue
        good, msg = move_local(a.get('taskId'), target, port)
        print(f'[拉] {a.get("taskId")} → {target}: {msg}')
        if good:
            ok += 1
            seen.add(key)
        else:
            fail += 1

    if not dry:
        # 只保留最近的 key，别让文件无限长
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump({'seen': list(seen)[-500:]}, f)
    print(f'[拉] 成功 {ok} / 失败 {fail} / 重复跳过 {skip}')
    return 0 if fail == 0 else 1


def main():
    args = sys.argv[1:]
    dry = '--dry-run' in args
    push_only = '--push-only' in args
    pull_only = '--pull-only' in args
    cfg = load_config()
    for k in ('base_url', 'sync_token'):
        if not cfg.get(k):
            print(f'配置缺 {k}', file=sys.stderr)
            return 2
    rc = 0
    if not pull_only:
        rc |= push(cfg, dry)
    if not push_only:
        rc |= pull(cfg, dry)
    return rc


if __name__ == '__main__':
    sys.exit(main())
