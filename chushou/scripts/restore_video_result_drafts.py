"""Restore completed result composers from immutable jobs, without generating media."""
import copy
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import quote, urlencode


def file_hash(workspace, relative):
    root = Path(workspace).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('原参考附件不存在或超出真实工作区：' + relative)
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def ordered_ids(value):
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return [item.strip() for item in str(value or '').split(',') if item.strip()]


def restore_result_drafts(client, source_workspace, target_workspace, document_id,
                          job_nodes, out, *, apply=False, repair_existing=False):
    """Create missing result drafts; repair_existing explicitly restores older fallback drafts.

    job_nodes is [(the actual original completed job, its independent result node ID)].
    References must match existing target media by actual file SHA, not just by name.
    Existing user edits are preserved by default. No workspace or task is mutated.
    """
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    workspace = client.api('/api/workspace/load', {'workspacePath': target_workspace, 'fresh': True})
    if workspace['state'].get('workspaceKind') != 'notebook':
        raise ValueError('成片操作栏目标必须是真实笔记工作区')
    nodes = {node['id']: node for node in workspace['state']['documents'][document_id]['canvas']['nodes']}
    hashes = {}
    unreadable = []
    for node in nodes.values():
        if node.get('file') and node.get('kind') in ('image', 'video', 'audio'):
            try:
                digest = file_hash(target_workspace, node['file'])
                hashes.setdefault(digest, []).append(node['id'])
            except ValueError:
                unreadable.append(node['id'])
    profiles = client.catalog()['channels']['video']['profiles']
    query = urlencode({'workspacePath': target_workspace, 'workspaceKind': 'notebook'})
    stored = client.api('/api/whiteboard/generation-drafts?' + query)
    before = copy.deepcopy(stored['cache'])
    (out / 'result-drafts-before.json').write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding='utf-8')
    cache = copy.deepcopy(before)
    records, errors, changed = [], [], {}
    source_hashes = {}
    for job, node_id in job_nodes:
        try:
            if job.get('status') != 'complete' or job.get('channel') != 'video':
                raise ValueError('仅恢复真实已完成的视频任务操作栏')
            if node_id not in nodes or nodes[node_id].get('kind') != 'video' or not nodes[node_id].get('file'):
                raise ValueError('独立结果节点不存在或未持有实际视频文件')
            request = job.get('request') or {}
            prompt = request.get('prompt') or ''
            if not prompt or not request.get('duration') or not request.get('aspectRatio'):
                raise ValueError('原任务缺少真实提示词、时长或比例')
            profile = request.get('settings') or {}
            connection_id = (profile.get('connectionId') or profile.get('id')
                             or (request.get('generationProfile') or {}).get('connectionId'))
            matches = [item for item in profiles if item.get('id') == connection_id]
            if len(matches) != 1:
                raise ValueError('原生成配置已不存在：' + str(connection_id))
            if any(profile.get(key) and profile[key] != matches[0].get(key)
                   for key in ('adapter', 'provider', 'protocol', 'dreaminaCliProfile')):
                raise ValueError('原生成配置ID的实际通道已经改变')
            references = request.get('referenceMedia') or []
            mapped = {}
            for media in references:
                old_id, relative = str(media.get('id') or ''), media.get('relativePath') or ''
                if not old_id or not relative:
                    raise ValueError('原引用缺少附件ID或真实路径')
                if relative not in source_hashes:
                    source_hashes[relative] = file_hash(source_workspace, relative)
                candidates = hashes.get(source_hashes[relative], [])
                if not candidates:
                    raise ValueError('笔记没有同SHA可用参考节点：' + old_id + ' · ' + str(media.get('name') or relative))
                mapped[old_id] = old_id if old_id in candidates else sorted(candidates, key=lambda value: ('-ref-' not in value, value))[0]
            order = ordered_ids(request.get('referenceOrder')) or [str(media['id']) for media in references]
            sequence = ordered_ids(request.get('promptReferenceSequence'))
            if any(item not in mapped for item in order + sequence):
                raise ValueError('原引用顺序含未映射节点ID')
            target_order = [mapped[item] for item in order]
            target_sequence = [mapped[item] for item in sequence]
            if len(order) != len(mapped) or len(set(order)) != len(order):
                raise ValueError('原引用顺序没有完整覆盖全部实际附件')
            values = {'prompt': prompt, 'connectionId': connection_id, 'model': profile.get('model') or (request.get('generationProfile') or {}).get('model'),
                'duration': str(request['duration']), 'aspectRatio': str(request['aspectRatio']),
                'explicitReferences': ','.join(target_order), 'referenceOrder': ','.join(target_order),
                'promptReferenceSequence': ','.join(target_sequence), 'includeTargetReference': ''}
            for field in ('generationMode', 'resolution', 'quality', 'background', 'videoCount', 'spec', 'multiframeDuration'):
                if request.get(field) not in (None, ''):
                    values[field] = str(request[field])
            if 'generateAudio' in request:
                values['generateAudio'] = request['generateAudio'] is True
            if 'multiframeTransitions' in request:
                values['multiframeTransitions'] = json.dumps(request['multiframeTransitions'], ensure_ascii=False) if not isinstance(request['multiframeTransitions'], str) else request['multiframeTransitions']
            scope = {'workspaceId': 'notebook:' + target_workspace.strip().lower(), 'documentId': document_id,
                     'nodeId': node_id, 'channel': 'video'}
            key = '::'.join(quote(scope[field], safe="~()*!.'-") for field in ('workspaceId', 'documentId', 'nodeId', 'channel'))
            previous = cache.setdefault('entries', {}).get(key)
            same = previous and all(previous.get('values', {}).get(field) == value for field, value in values.items())
            status = 'already_matches_original' if same else ('user_draft_preserved' if previous and not repair_existing else 'planned')
            record = {'jobId': job['id'], 'nodeId': node_id, 'key': key, 'duration': values['duration'],
                      'aspectRatio': values['aspectRatio'], 'connectionId': connection_id,
                      'referenceNodeIds': target_order, 'referenceCount': len(target_order),
                      'promptCharacters': len(prompt), 'status': status}
            records.append(record)
            if status == 'planned':
                updated = max(int(time.time()*1000), int((previous or {}).get('updatedAt') or 0) + 1)
                entry = {**(previous or {}), **scope, 'updatedAt': updated,
                         'values': {**(previous or {}).get('values', {}), **values}}
                cache['entries'][key] = entry
                changed[key] = entry
        except (ValueError, KeyError, OSError) as error:
            errors.append({'jobId': job['id'], 'nodeId': node_id, 'error': str(error)})
    if apply and changed:
        client.api('/api/whiteboard/generation-drafts', {'workspacePath': target_workspace, 'workspaceKind': 'notebook',
                   'sessionUpdatedAt': stored.get('sessionUpdatedAt', 0), 'cache': cache})
        after = client.api('/api/whiteboard/generation-drafts?' + query)
        (out / 'result-drafts-after.json').write_text(json.dumps(after, ensure_ascii=False, indent=2), encoding='utf-8')
        for key, entry in changed.items():
            if after['cache']['entries'].get(key, {}).get('values') != entry['values']:
                raise ValueError('成片操作栏真实回读失败：' + entry['nodeId'])
        for key, entry in before.get('entries', {}).items():
            if key not in changed and after['cache']['entries'].get(key) != entry:
                raise ValueError('其他节点操作栏发生变化：' + entry.get('nodeId', key))
        if any(before.get(field) != after['cache'].get(field) for field in ('active', 'openSessions')):
            raise ValueError('当前操作栏会话发生变化')
        for record in records:
            if record['status'] == 'planned':
                record['status'] = 'restored'
    result = {'applied': bool(apply and changed), 'changedEntries': len(changed), 'records': records,
              'unresolvedDrafts': errors, 'unreadableExistingMedia': unreadable,
              'otherEntriesPreserved': True, 'activePreserved': True, 'openSessionsPreserved': True,
              'workspaceChanged': False, 'jobsChanged': False, 'generationSubmitted': False}
    (out / 'result-drafts-report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result
