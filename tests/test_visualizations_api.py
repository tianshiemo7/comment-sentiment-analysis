"""Real experiment figures: missing artifacts, model isolation and file boundaries."""
import base64
import json
import os

import pytest
from fastapi.testclient import TestClient

from src.inference import visualizations
from src.inference.app import app

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a0gAAAABJRU5ErkJggg==')


@pytest.fixture
def gallery(tmp_path, monkeypatch):
    for name in ('FIGURES_DIR', 'STATS_DIR', 'CHECKPOINT_DIR'):
        directory = tmp_path / name.lower()
        directory.mkdir()
        monkeypatch.setattr(visualizations, name, directory)
    # Avoid lifespan startup; these read-only endpoints do not need worker queues.
    return TestClient(app)


def test_missing_artifacts_are_explicit_not_simulated(gallery):
    response = gallery.get('/api/visualizations')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    data = response.json()
    assert data['model'] == 'textcnn' and len(data['figures']) == 5
    assert data['best_epoch'] is None and data['dataset_version'] is None
    assert all(value is None for value in data['metrics'].values())
    for figure in data['figures']:
        assert not figure['available']
        assert figure['image_url'] is figure['download_url'] is figure['updated_at'] is None


def test_models_have_distinct_figures_and_share_only_corpus_clouds(gallery):
    figures = visualizations.FIGURES_DIR
    (figures / 'loss_curve_textcnn.png').write_bytes(PNG)
    (figures / 'wordcloud_positive.png').write_bytes(PNG)
    # A legacy "latest model" plot must never be mislabeled as the selected model.
    (figures / 'loss_curve.png').write_bytes(PNG)
    (visualizations.STATS_DIR / 'metrics_textcnn.json').write_text(json.dumps({'metrics': {'accuracy': .896, 'f1': .8876}}))
    checkpoint = visualizations.CHECKPOINT_DIR / 'textcnn'
    (checkpoint / 'preprocessing').mkdir(parents=True)
    (checkpoint / 'metadata.json').write_text('{"epoch": 2}')
    (checkpoint / 'preprocessing/manifest.json').write_text('{"dataset_version":"example-v1"}')
    cnn = gallery.get('/api/visualizations?model=textcnn').json()
    lstm = gallery.get('/api/visualizations?model=bilstm_attention').json()
    assert cnn['metrics']['accuracy'] == .896 and cnn['metrics']['precision'] is None
    assert cnn['best_epoch'] == 2 and cnn['dataset_version'] == 'example-v1'
    assert lstm['best_epoch'] is None and len(lstm['figures']) == 6
    assert {f['id'] for f in cnn['figures'] if f['available']} == {'textcnn-loss', 'wordcloud-positive'}
    assert {f['id'] for f in lstm['figures'] if f['available']} == {'wordcloud-positive'}
    assert all(f['id'].startswith('bilstm_attention-') for f in lstm['figures'] if f['scope'] == 'model')
    assert str(figures) not in json.dumps(cnn)


def test_listed_images_can_be_viewed_and_downloaded(gallery):
    (visualizations.FIGURES_DIR / 'accuracy_curve_textcnn.png').write_bytes(PNG)
    item = next(f for f in gallery.get('/api/visualizations').json()['figures'] if f['id'] == 'textcnn-accuracy')
    for key, disposition in (('image_url', 'inline'), ('download_url', 'attachment')):
        response = gallery.get(item[key])
        assert response.status_code == 200 and response.content == PNG
        assert response.headers['content-type'] == 'image/png'
        assert response.headers['content-disposition'].startswith(disposition)
        assert 'accuracy_curve_textcnn.png' in response.headers['content-disposition']
        assert response.headers['cache-control'] == 'no-cache'


def test_refresh_revisions_follow_regenerated_files(gallery):
    path = visualizations.FIGURES_DIR / 'loss_curve_textcnn.png'
    path.write_bytes(PNG)
    first = gallery.get('/api/visualizations').json()['figures'][0]
    stamp = path.stat().st_mtime_ns
    os.utime(path, ns=(stamp + 2_000_000_000, stamp + 2_000_000_000))
    second = gallery.get('/api/visualizations').json()['figures'][0]
    assert first['image_url'] != second['image_url']
    assert first['updated_at'] != second['updated_at']
    path.unlink()
    missing = gallery.get('/api/visualizations').json()['figures'][0]
    assert not missing['available'] and missing['image_url'] is None
    assert gallery.get(first['image_url']).status_code == 404


@pytest.mark.parametrize('model', ['bert', '../textcnn', '../../outputs/settings', 'TextCNN'])
def test_unknown_models_are_rejected(gallery, model):
    assert gallery.get('/api/visualizations', params={'model': model}).status_code == 400


@pytest.mark.parametrize('figure_id', ['settings.json', 'vocab.json', 'loss_curve.png', 'textcnn-loss'])
def test_unknown_ids_and_absent_images_do_not_expose_other_files(gallery, figure_id):
    (visualizations.FIGURES_DIR / 'settings.json').write_text('{"private":"secret"}')
    response = gallery.get(f'/api/visualizations/figures/{figure_id}')
    assert response.status_code == 404 and 'secret' not in response.text


def test_directory_and_symbolic_link_are_not_downloadable(gallery, monkeypatch):
    path = visualizations.FIGURES_DIR / 'loss_curve_textcnn.png'
    path.mkdir()
    assert gallery.get('/api/visualizations/figures/textcnn-loss').status_code == 404
    path.rmdir()
    path.write_bytes(PNG)
    # Windows often cannot create links without elevation; exercise the same
    # path guard deterministically rather than skipping this boundary check.
    original = type(path).is_symlink
    monkeypatch.setattr(type(path), 'is_symlink', lambda self: self == path or original(self))
    assert not gallery.get('/api/visualizations').json()['figures'][0]['available']
    assert gallery.get('/api/visualizations/figures/textcnn-loss').status_code == 404


def test_corrupt_optional_metadata_does_not_break_the_gallery(gallery):
    stats = visualizations.STATS_DIR / 'metrics_textcnn.json'
    stats.write_text('{broken')
    assert gallery.get('/api/visualizations').status_code == 200
    stats.write_text(json.dumps({'metrics': {'accuracy': float('nan'), 'f1': True, 'precision': 2, 'recall': -.1}}))
    assert all(value is None for value in gallery.get('/api/visualizations').json()['metrics'].values())
    stats.write_text('{"metrics": []}')
    assert gallery.get('/api/visualizations').status_code == 200
