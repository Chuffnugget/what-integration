"""Parse service metadata strictly before it reaches Hassfest or the HA UI."""
import ast
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1] / 'custom_components' / 'what'


class UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
        if len(keys) != len(set(keys)):
            raise ValueError('Duplicate YAML mapping key')
        return super().construct_mapping(node, deep=deep)


@pytest.fixture
def descriptions():
    return yaml.load((ROOT / 'services.yaml').read_text(encoding='utf-8'), Loader=UniqueKeyLoader)


def test_service_fields_have_one_valid_selector(descriptions):
    for service in descriptions.values():
        # HA service targets support entity filters; device selection is a field.
        assert 'device' not in service.get('target', {})
        for field in service['fields'].values():
            selector = field['selector']
            assert len(selector) == 1
            if 'select' in selector:
                assert isinstance(selector['select']['options'], list)
                assert selector['select']['options']


@pytest.mark.parametrize(
    'name',
    ['run_command', 'send_notification', 'send_keyboard', 'send_mouse', 'wallpaper_engine'],
)
def test_pc_services_offer_required_device_selector(descriptions, name):
    field = descriptions[name]['fields']['device_id']
    assert field['required'] is True
    assert field['selector']['device']['integration'] == 'what'
    assert not field['selector']['device'].get('multiple', False)


def test_wallpaper_operation_choices_and_paths_match_service(descriptions):
    tree = ast.parse((ROOT / 'wallpaper.py').read_text(encoding='utf-8'))
    operations = next(ast.literal_eval(node.value) for node in tree.body
                      if (
                          isinstance(node, ast.Assign)
                          and any(
                              (isinstance(target, ast.Name) and target.id == 'OPERATIONS' for target in node.targets),
                          )
                      ))
    fields = descriptions['wallpaper_engine']['fields']
    assert set(fields['operation']['selector']['select']['options']) == set(operations)
    for name in ('file', 'playlist', 'profile'):
        assert fields[name]['selector'] == {'text': None}
    assert fields['options']['selector'] == {'object': None}


def test_service_fields_have_names_and_help(descriptions):
    import json

    strings = json.loads((ROOT / "strings.json").read_text(encoding="utf-8"))
    english = json.loads((ROOT / "translations/en.json").read_text(encoding="utf-8"))
    assert strings == english
    for name, service in descriptions.items():
        for key, field in service["fields"].items():
            translated = strings["services"][name]["fields"][key]
            assert field["name"] and field["description"], (name, key)
            assert translated["name"] and translated["description"], (name, key)
