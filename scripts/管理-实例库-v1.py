"""本地写作实例的创建、认可、撤回和检索；仅使用标准库。"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import uuid

FOLDERS = ('基础实例库', '重要但未解决示例', '待验证', '成长实例库')
COMMON = ('id', 'title', 'genre', 'source', 'user_prompt_raw', 'original_excerpt', 'diagnosis', 'scope')
DRAFT = ('revised_excerpt', 'artifact_version')


def read(path):
    value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('输入必须是JSON对象')
    return value


def strings(value, fields):
    for field in fields:
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise ValueError(f'字段缺失或为空：{field}')


def identity(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,79}', value):
        raise ValueError('实例ID只能包含字母、数字和短横线')
    return value


def feedback(value, version=None):
    strings(value, ('verbatim', 'source'))
    if value.get('human_confirmed') is not True:
        raise ValueError('必须核对真实用户明确反馈，AI自评不能作为认可')
    if version is not None:
        strings(value, ('artifact_version', 'scope'))
        if value['artifact_version'] != version:
            raise ValueError('用户反馈所指版本与候选版本不一致')


def validate(value, folder):
    strings(value, COMMON)
    identity(value['id'])
    tags = value.get('tags')
    if not isinstance(tags, list) or not tags or any(not isinstance(t, str) or not t.strip() for t in tags):
        raise ValueError('tags必须是非空字符串数组')
    expected = {'基础实例库': ('baseline',), '重要但未解决示例': ('unresolved',), '待验证': ('pending',), '成长实例库': ('accepted', 'withdrawn')}
    if value.get('status') not in expected[folder]:
        raise ValueError(f'记录状态与目录不匹配：{folder}')
    if folder in ('基础实例库', '重要但未解决示例'):
        strings(value, ('repair_direction',))
    else:
        strings(value, DRAFT)
        if not any(isinstance(value.get(k), str) and value[k].strip()
                   for k in ('revision_instruction', 'generated_prompt')):
            raise ValueError('需保留实际修订指令；完整提示词仅在实际使用时记录')
        if folder == '成长实例库':
            feedback(value.get('approval', {}), value['artifact_version'])
            if value['status'] == 'withdrawn':
                feedback(value.get('withdrawal', {}))


def records(root):
    for folder in FOLDERS:
        for path in sorted((root / '实例库' / folder).glob('*.json')):
            yield folder, path, read(path)


def now():
    return datetime.now(timezone.utc).isoformat()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()


@contextmanager
def locked(root):
    library = root / '实例库'
    library.mkdir(parents=True, exist_ok=True)
    lock = library / '.写入锁'
    try:
        lock.mkdir()
    except FileExistsError:
        raise ValueError('实例库正在写入；如先前异常退出，先核实没有写入进程再清理锁目录')
    try:
        yield
    finally:
        lock.rmdir()


def find(root, ident, folder):
    matched = [(p, r) for f, p, r in records(root) if f == folder and r.get('id') == ident]
    if len(matched) != 1:
        raise ValueError(f'{folder}中必须恰有一条实例：{ident}')
    validate(matched[0][1], folder)
    return matched[0]


def run(args):
    root = args.root.resolve()
    if args.action == 'list':
        result = []
        for folder, path, rec in records(root):
            if args.pending:
                if folder != '待验证':
                    continue
            elif args.unresolved:
                if folder != '重要但未解决示例':
                    continue
            elif rec.get('status') not in ('baseline', 'accepted'):
                continue
            if args.query.casefold() not in json.dumps(rec, ensure_ascii=False).casefold():
                continue
            result.append({'id': rec['id'], 'title': rec['title'], 'status': rec['status'],
                           'outcome_status': rec.get('outcome_status', rec['status']),
                           'tags': rec['tags'], 'path': str(path)})
        return result
    if args.action == 'check':
        seen = set()
        counts = {folder: 0 for folder in FOLDERS}
        for folder, path, rec in records(root):
            validate(rec, folder)
            if rec['id'] in seen:
                raise ValueError(f'重复实例ID：{rec["id"]}')
            seen.add(rec['id'])
            counts[folder] += 1
        return {'result': 'PASS', 'counts': counts}
    with locked(root):
        if args.action == 'add':
            rec = read(args.input)
            if any(k in rec for k in ('approval', 'withdrawal', 'accepted_at')):
                raise ValueError('新增候选不能预置认可或撤回记录')
            if rec.get('status', 'pending') != 'pending':
                raise ValueError('新增返工必须先进入待验证状态')
            rec['status'] = 'pending'
            validate(rec, '待验证')
            for _, _, old in records(root):
                if old['id'] == rec['id']:
                    raise ValueError('实例ID已存在；不会覆盖原话或旧版本')
                if old.get('user_prompt_raw') == rec['user_prompt_raw'] and old.get('artifact_version') == rec['artifact_version']:
                    raise ValueError('相同原话及版本已经存在')
            rec['created_at'] = now()
            dest = root / '实例库' / '待验证' / f'案例-{rec["id"]}-v1.json'
            write(dest, rec)
            return {'result': 'pending', 'id': rec['id'], 'path': str(dest)}
        identity(args.id)
        fb = read(args.feedback)
        if args.action == 'accept':
            path, rec = find(root, args.id, '待验证')
            feedback(fb, rec['artifact_version'])
            dest = root / '实例库' / '成长实例库' / path.name
            if dest.exists() or any(f == '成长实例库' and r['id'] == args.id for f, _, r in records(root)):
                raise ValueError('成长库已含此ID；请检查先前写入，不覆盖认可记录')
            rec.update(status='accepted', approval=fb, accepted_at=now())
            validate(rec, '成长实例库')
            write(dest, rec)
            path.unlink()
            return {'result': 'accepted', 'id': args.id, 'path': str(dest)}
        if args.action == 'withdraw':
            path, rec = find(root, args.id, '成长实例库')
            if rec['status'] != 'accepted':
                raise ValueError('此实例已经撤回；原始记录保持不变')
            feedback(fb)
            rec.update(status='withdrawn', withdrawal=fb, withdrawn_at=now())
            write(path, rec)
            return {'result': 'withdrawn', 'id': args.id, 'path': str(path)}
    raise ValueError('未知操作')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('list'); p.add_argument('--query', default='')
    group = p.add_mutually_exclusive_group()
    group.add_argument('--pending', action='store_true')
    group.add_argument('--unresolved', action='store_true')
    sub.add_parser('check')
    p = sub.add_parser('add'); p.add_argument('--input', type=Path, required=True)
    for action in ('accept', 'withdraw'):
        p = sub.add_parser(action); p.add_argument('--id', required=True); p.add_argument('--feedback', type=Path, required=True)
    try:
        result = run(parser.parse_args())
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
