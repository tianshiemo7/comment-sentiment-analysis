import csv
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from src.crawler import spider, sources, bilibili

BV = 'BV1AJ411g7Fr'


def comment(rpid, text='一条评论', count=0):
    return {'rpid': rpid, 'content': {'message': text}, 'rcount': count, 'ctime': 1600000000}


def response(data=None, status=200, text=''):
    obj = requests.Response()
    obj.status_code = status
    obj.url = 'https://api.bilibili.com/test'
    obj.encoding = 'utf-8'
    obj._content = (json.dumps(data) if data is not None else text).encode()
    return obj


@pytest.fixture
def network(monkeypatch):
    state = {'calls': [], 'headers': [], 'main': [comment(1, count=1), comment(2)],
             'sub': [comment(3, '楼中楼')], 'error': None, 'end': True,
             'robots': response(status=404)}

    def get(session, url, **kwargs):
        state['calls'].append((url, kwargs))
        state['headers'].append(dict(session.headers))
        if url.endswith('/robots.txt'):
            return state['robots']
        if url.endswith('/view'):
            data = {'aid': 68522639, 'title': '测试视频'}
        elif url.endswith('/nav'):
            data = {'wbi_img': {'img_url': 'https://i.example/' + 'a' * 32 + '.png',
                                'sub_url': 'https://i.example/' + 'b' * 32 + '.png'}}
        elif url.endswith('/wbi/main'):
            data = {'replies': state['main'], 'cursor': {'is_end': state['end'],
                    'pagination_reply': {'next_offset': 'next-page'}}}
            if state.get('pages'):
                data = state['pages'].pop(0)
        elif url.endswith('/reply/reply'):
            if state['error']:
                return response({'code': state['error'], 'message': str(state['error'])})
            data = {'replies': state['sub'], 'page': {'count': len(state['sub'])}}
        else:
            raise AssertionError(url)
        return response({'code': 0, 'data': data})

    monkeypatch.setattr(requests.Session, 'get', get)
    monkeypatch.setattr(bilibili.time, 'sleep', lambda _: None)
    return state


def config(tmp_path, **kwargs):
    values = dict(start_urls=[BV], source='bilibili', out_path=tmp_path / 'bilibili.csv',
                  delay_seconds=2, extra={'include_replies': True})
    values.update(kwargs)
    return spider.CrawlConfig(**values)


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def test_framework_crawls_main_and_sub_with_provenance(tmp_path, network):
    cfg = config(tmp_path)
    items = spider.crawl(cfg)
    assert len(items) == 3
    assert all(set(spider.RAW_FIELDS) <= row.keys() for row in items)
    assert all(row['label'] is None and row['source'] == 'bilibili' for row in items)
    sub = next(row for row in items if row['level'] == 2)
    assert sub['root_rpid'] == '1'
    assert BV in sub['url']
    assert len(read_csv(cfg.out_path)) == 3
    note = (tmp_path / '来源说明.md').read_text(encoding='utf-8')
    assert BV in note and '未标注' in note
    assert spider.crawl(cfg) == []
    assert len(read_csv(cfg.out_path)) == 3


def test_limit_and_resume_do_not_skip_partial_page(tmp_path, network):
    cfg = config(tmp_path, max_items=1)
    assert len(spider.crawl(cfg)) == 1
    cfg.max_items = 10
    assert len(spider.crawl(cfg)) == 2
    assert {r['rpid'] for r in read_csv(cfg.out_path)} == {'1', '2', '3'}


def test_risk_control_preserves_data_and_page_for_resume(tmp_path, network):
    cfg = config(tmp_path)
    network['error'] = -352
    with pytest.raises(bilibili.CrawlStopped, match='风控'):
        spider.crawl(cfg)
    assert len(read_csv(cfg.out_path)) == 2
    assert '未完成' in (tmp_path / '来源说明.md').read_text(encoding='utf-8')
    network['error'] = None
    items = spider.crawl(cfg)
    assert len(items) == 1 and items[0]['level'] == 2


def test_checkpoint_config_change_requires_reset(tmp_path, network):
    cfg = config(tmp_path)
    spider.crawl(cfg)
    cfg.extra['include_replies'] = False
    with pytest.raises(ValueError, match='reset-state'):
        spider.crawl(cfg)
    cfg.extra['reset_state'] = True
    assert spider.crawl(cfg) == []


def test_checkpoint_isolated_by_output(tmp_path, network):
    spider.crawl(config(tmp_path))
    assert len(spider.crawl(config(tmp_path, out_path=tmp_path / 'other.csv'))) == 3


def test_robots_denial_prevents_api_request(tmp_path, network):
    network['robots'] = response(text='User-agent: *\nDisallow: /\n')
    with pytest.raises(bilibili.CrawlStopped, match='robots'):
        spider.crawl(config(tmp_path))
    assert all(url.endswith('/robots.txt') for url, _ in network['calls'])


def test_request_uses_config_timeout_and_user_agent(tmp_path, network):
    cfg = config(tmp_path, timeout=7, user_agent='course-test/1.0')
    spider.crawl(cfg)
    assert all(args['timeout'] == 7 for _, args in network['calls'])
    assert all(headers['User-Agent'] == 'course-test/1.0' for headers in network['headers'])


@pytest.mark.parametrize('overrides', [{'max_items': 0}, {'timeout': 0},
                                     {'delay_seconds': float('nan')}, {'delay_seconds': -1}])
def test_invalid_config(overrides, tmp_path):
    with pytest.raises(ValueError):
        config(tmp_path, **overrides)


def test_source_note_appends_and_escapes(tmp_path):
    path = tmp_path / '来源说明.md'
    record = sources.SourceRecord('B|站', 'https://example.com', '2026-10-16', 2,
                                  labeled=False, note='第一行\n第二行')
    sources.write_source_note([record], path)
    sources.write_source_note([record], path)
    content = path.read_text(encoding='utf-8')
    assert content.count('B&#124;站') == 2
    assert content.count('| 站点或数据集 |') == 1
    assert '未标注' in content


def test_save_raw_preserves_unicode_and_standard_fields(tmp_path):
    row = dict(text='评论,中文\n下一行', label=None, source='test', url='https://example.com', crawled_at='now')
    path = spider.save_raw(iter([row]), tmp_path / 'out.csv')
    assert read_csv(path)[0]['text'] == row['text']


def test_parse_html_comments_requires_selector_and_filters_noise():
    html = '<p class="comment">很好<button>回复</button><script>bad()</script></p><p class="comment">!!!</p>'
    assert spider.parse_comments(html, '.comment') == ['很好']
    with pytest.raises(ValueError, match='selector'):
        spider.parse_comments(html)


def test_cli_dispatch_and_prompt(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(spider, 'crawl', lambda cfg: calls.append(cfg) or [])
    monkeypatch.setattr('builtins.input', lambda _: BV)
    assert spider.main(['--include-replies', '--out', str(tmp_path / 'out.csv')]) == 0
    assert calls[0].start_urls == [BV]
    assert calls[0].extra['include_replies'] is True
    assert spider.main(['--bvid', BV, '--max-pages', '1']) == 0


def test_existing_output_schema_is_not_silently_overwritten(tmp_path, network):
    cfg = config(tmp_path)
    cfg.out_path.write_text('text\n旧数据\n', encoding='utf-8')
    with pytest.raises(ValueError, match='字段'):
        spider.crawl(cfg)
    assert cfg.out_path.read_text(encoding='utf-8') == 'text\n旧数据\n'


def test_multiple_main_pages_use_cursor_and_resume(tmp_path, network):
    network['pages'] = [
        {'replies': [comment(10)], 'cursor': {'is_end': False, 'pagination_reply': {'next_offset': 'cursor-2'}}},
        {'replies': [comment(11)], 'cursor': {'is_end': True}},
    ]
    cfg = config(tmp_path, extra={'max_pages': 1})
    assert [row['rpid'] for row in spider.crawl(cfg)] == ['10']
    assert [row['rpid'] for row in spider.crawl(cfg)] == ['11']
    params = [args['params'] for url, args in network['calls'] if url.endswith('/wbi/main')]
    assert json.loads(params[1]['pagination_str']) == {'offset': 'cursor-2'}


def test_global_limit_across_videos(tmp_path, network):
    cfg = config(tmp_path, start_urls=[BV, 'BV1UB4y1W7Xu'], max_items=4)
    assert len(spider.crawl(cfg)) == 4
    assert len(read_csv(cfg.out_path)) == 4
    cfg.max_items = 10
    assert len(spider.crawl(cfg)) == 2
    assert len(read_csv(cfg.out_path)) == 6


def test_missing_output_restarts_despite_finished_checkpoint(tmp_path, network):
    cfg = config(tmp_path)
    spider.crawl(cfg)
    cfg.out_path.unlink()
    assert len(spider.crawl(cfg)) == 3


@pytest.mark.parametrize('status', [403, 500, 302])
def test_robots_unavailable_stops_before_api(tmp_path, network, status):
    network['robots'] = response(status=status)
    with pytest.raises(bilibili.CrawlStopped, match='robots'):
        spider.crawl(config(tmp_path))
    assert len(network['calls']) == 1


def test_api_352_is_not_retried(tmp_path, network):
    network['error'] = -352
    with pytest.raises(bilibili.CrawlStopped):
        spider.crawl(config(tmp_path))
    assert sum(url.endswith('/reply/reply') for url, _ in network['calls']) == 1


def test_cli_noninteractive_and_empty_input(monkeypatch, capsys):
    def eof(_):
        raise EOFError()
    monkeypatch.setattr('builtins.input', eof)
    with pytest.raises(SystemExit) as exc:
        spider.main([])
    assert exc.value.code == 2
    assert '终端' in capsys.readouterr().err
    monkeypatch.setattr('builtins.input', lambda _: '   ')
    with pytest.raises(SystemExit) as exc:
        spider.main([])
    assert exc.value.code == 2


def test_generic_html_pipeline_deduplicates_and_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(spider, 'fetch_html', lambda *args: '<p>评论甲</p><p>评论甲</p><p>评论乙</p>')
    cfg = config(tmp_path, source='html', start_urls=['https://example.com'],
                 max_items=1, extra={'selector': 'p'})
    assert [row['text'] for row in spider.crawl(cfg)] == ['评论甲']
    assert [row['text'] for row in spider.crawl(cfg)] == ['评论乙']


@pytest.mark.parametrize('entry', ['module', 'file'])
def test_real_framework_entry_limit_is_success(tmp_path, entry):
    # runpy reproduces __main__ identity, unlike directly calling spider.main().
    root = Path(__file__).resolve().parents[1]
    program = '''
import runpy, sys
from src.crawler import bilibili
bilibili.BiliClient.get_video_info = lambda *args: (1, 'video')
bilibili.BiliClient.get_main_comments = lambda *args, **kwargs: {
    'replies': [{'rpid': 1, 'rcount': 1, 'content': {'message': 'hello'}}],
    'cursor': {'is_end': True}}
sys.argv = ['spider.py', '--bvid', 'BV1AJ411g7Fr', '--include-replies', '--max-items', '1', '--out', sys.argv[1]]
'''
    if entry == 'module':
        program += "runpy.run_module('src.crawler.spider', run_name='__main__')"
    else:
        program += "runpy.run_path('src/crawler/spider.py', run_name='__main__')"
    result = subprocess.run([sys.executable, '-B', '-c', program, str(tmp_path / 'out.csv')],
                            cwd=root, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode(errors='replace')


def test_html_limit_does_not_claim_unvisited_url_completed(tmp_path, monkeypatch):
    calls = []
    def fetch(url, cfg):
        calls.append(url)
        return '<p>一条评论</p>'
    monkeypatch.setattr(spider, 'fetch_html', fetch)
    cfg = config(tmp_path, source='html', start_urls=['https://example.com/one', 'https://example.com/two'],
                 max_items=1, extra={'selector': 'p'})
    spider.crawl(cfg)
    note = (tmp_path / '来源说明.md').read_text(encoding='utf-8')
    assert calls == ['https://example.com/one']
    assert '达到本次新增条数上限' in note
    second = next(line for line in note.splitlines() if 'https://example.com/two' in line)
    assert '未访问' in second


def test_html_throttles_robots_between_pages(tmp_path, monkeypatch):
    events = []
    def get(session, url, **kwargs):
        events.append(url)
        return response(status=404) if url.endswith('/robots.txt') else response(text='<p>ok</p>')
    monkeypatch.setattr(requests.Session, 'get', get)
    monkeypatch.setattr(spider.time, 'sleep', lambda delay: events.append(delay))
    cfg = config(tmp_path, source='html', extra={'selector': 'p'})
    spider.fetch_html('https://example.com/one', cfg)
    spider.fetch_html('https://example.com/two', cfg)
    index = events.index('https://example.com/one')
    assert events[index + 1] == cfg.delay_seconds


def test_explicit_robots_denial_is_distinguished_from_network_failure(tmp_path, network):
    network['robots'] = response(text='User-agent: *\nDisallow: /\n')
    with pytest.raises(bilibili.CrawlStopped, match='robots.txt 明确禁止') as exc:
        spider.crawl(config(tmp_path))
    assert 'https://api.bilibili.com/robots.txt' in str(exc.value)
    note = (tmp_path / '来源说明.md').read_text(encoding='utf-8')
    assert '明确禁止' in note


def test_robots_http_failure_explains_status(tmp_path, network):
    network['robots'] = response(status=503)
    with pytest.raises(bilibili.CrawlStopped, match='HTTP 503'):
        spider.crawl(config(tmp_path))


def test_robots_boolean_public_api_remains_compatible(network):
    network['robots'] = response(text='User-agent: *\nDisallow: /\n')
    assert spider.is_allowed_by_robots('https://api.bilibili.com/x/web-interface/view') is False


def test_skip_robots_bilibili_fetches_comments_and_records_policy(tmp_path, network):
    network['robots'] = response(text='User-agent: *\nDisallow: /\n')
    items = spider.crawl(config(tmp_path, respect_robots=False))
    assert len(items) == 3
    assert not any(url.endswith('/robots.txt') for url, _ in network['calls'])
    assert 'robots.txt 检查：已跳过' in (tmp_path / '来源说明.md').read_text(encoding='utf-8')


def test_skip_robots_html_fetches_directly(tmp_path, monkeypatch):
    calls = []
    def get(session, url, **kwargs):
        calls.append(url)
        return response(text='<p>comment</p>')
    monkeypatch.setattr(requests.Session, 'get', get)
    monkeypatch.setattr(spider.time, 'sleep', lambda _: None)
    cfg = config(tmp_path, source='html', respect_robots=False)
    assert 'comment' in spider.fetch_html('https://example.com/comments', cfg)
    assert calls == ['https://example.com/comments']


def test_cli_skip_robots_is_explicit_opt_in(monkeypatch):
    configs = []
    monkeypatch.setattr(spider, 'crawl', lambda cfg: configs.append(cfg) or [])
    assert spider.main(['--bvid', BV, '--skip-robots']) == 0
    assert configs[-1].respect_robots is False
    assert spider.main(['--bvid', BV]) == 0
    assert configs[-1].respect_robots is True
