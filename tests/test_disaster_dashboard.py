import json
import os
import tempfile
import unittest
from unittest.mock import patch

import app as app_module


class DisasterDashboardTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config['TESTING'] = True
        self.client = app_module.app.test_client()

    def csrf_token(self):
        with self.client.session_transaction() as current_session:
            return current_session['_csrf_token']

    def test_disaster_information_is_sorted_and_filterable(self):
        records = [
            {
                'timestamp': '2025年06月10日 19:04',
                'area_name': '藤沢市',
                'warnings': [{'name': '雷注意報'}],
                'category': 'information'
            },
            {
                'timestamp': '2025年06月15日 09:55',
                'area_name': '藤沢市',
                'warnings': [],
                'category': 'emergency'
            }
        ]
        with patch.object(app_module, 'get_disaster_information', return_value=(records, False)):
            response = self.client.get('/disaster_information?category=emergency')

        self.assertEqual(response.status_code, 200)
        self.assertIn('2025年06月15日 09:55'.encode(), response.data)
        self.assertNotIn('2025年06月10日 19:04'.encode(), response.data)
        self.assertIn('aria-current="page"'.encode(), response.data)

    def test_current_page_name_is_shown_before_hamburger_menu(self):
        response = self.client.get('/shelter_search')

        self.assertEqual(response.status_code, 200)
        page_name_position = response.data.index('class="current-page-name"'.encode())
        menu_button_position = response.data.index('class="menu-toggle"'.encode())
        self.assertLess(page_name_position, menu_button_position)
        self.assertIn('避難所検索'.encode(), response.data[page_name_position:menu_button_position])

    def test_disaster_history_read_error_is_not_shown_as_empty_success(self):
        with patch.object(app_module, 'get_disaster_information', return_value=([], True)):
            response = self.client.get('/disaster_information')

        self.assertEqual(response.status_code, 200)
        self.assertIn('災害情報を読み込めませんでした'.encode(), response.data)
        self.assertNotIn('該当する災害情報はありません'.encode(), response.data)

    def test_home_separates_active_instructions_from_disaster_information(self):
        active = {
            'id': 10,
            'target': '住民',
            'content': '高台へ避難してください',
            'urgency': '緊急',
            'shelter': '中央中学校',
            'status': '有効',
            'created_at': '2026年10月04日 12:00'
        }
        inactive = {**active, 'id': 11, 'content': '解除済み指示', 'status': '解除'}
        with (
            patch.object(app_module, 'instructions', [active, inactive]),
            patch.object(
                app_module,
                'get_disaster_information',
                return_value=([{
                    'category': 'information',
                    'area_name': '藤沢市',
                    'timestamp': '2026年10月04日 11:00',
                    'warnings': []
                }], False)
            ),
            patch.object(app_module, 'shelter_data_error', False)
        ):
            response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertIn('高台へ避難してください'.encode(), response.data)
        self.assertIn('災害情報（状況のお知らせ）'.encode(), response.data)
        self.assertNotIn('解除済み指示'.encode(), response.data)
        self.assertIn('避難所マップ'.encode(), response.data)

    def test_map_omits_shelters_without_verified_coordinates(self):
        with patch.object(
            app_module,
            'shelters',
            [
                {'id': 1, 'name': '未確認施設'},
                {'id': 2, 'name': '不正な位置', 'latitude': True, 'longitude': 139.0},
                {'id': 3, 'name': '位置登録済み', 'latitude': 35.0, 'longitude': 139.0}
            ]
        ):
            map_shelters = app_module.get_map_shelters()

        self.assertEqual([item['id'] for item in map_shelters], [3])
        self.assertEqual(map_shelters[0]['opening_status'], '未登録')

    def test_home_has_location_button_and_unconfirmed_location_notice(self):
        with (
            patch.object(app_module, 'shelters', [{'id': 1, 'name': '座標なし'}]),
            patch.object(app_module, 'shelter_data_error', False),
            patch.object(app_module, 'get_disaster_information', return_value=([], False))
        ):
            response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertIn('現在地を地図に表示'.encode(), response.data)
        self.assertIn('位置情報が登録された避難所はありません'.encode(), response.data)
        self.assertIn('PERMISSION_DENIED'.encode(), response.data)
        self.assertIn('10分間隔'.encode(), response.data)
        self.assertIn('refreshWeather'.encode(), response.data)

    def test_board_requires_login_for_read_and_write(self):
        response = self.client.get('/board')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.location)

        response = self.client.post('/board', data={'action': 'create'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.location)

    def test_staff_can_publish_valid_resident_instruction(self):
        with (
            patch.object(app_module, 'instructions', []),
            patch.object(app_module, 'save_instructions') as save
        ):
            with self.client.session_transaction() as current_session:
                current_session['logged_in'] = True
            self.client.get('/board')
            response = self.client.post('/board', data={
                'csrf_token': self.csrf_token(),
                'action': 'create',
                'content': '安全な場所へ避難してください',
                'urgency': '緊急',
                'shelter': '中央中学校'
            })

            self.assertEqual(response.status_code, 302)
            self.assertEqual(len(app_module.instructions), 1)
            self.assertEqual(app_module.instructions[0]['status'], '有効')
            self.assertEqual(app_module.instructions[0]['urgency'], '緊急')
            self.assertEqual(app_module.instructions[0]['shelter'], '中央中学校')
            save.assert_called_once()

    def test_invalid_csrf_and_instruction_input_are_rejected(self):
        with patch.object(app_module, 'instructions', []), patch.object(app_module, 'save_instructions') as save:
            with self.client.session_transaction() as current_session:
                current_session['logged_in'] = True
            self.client.get('/board')
            response = self.client.post('/board', data={
                'csrf_token': 'invalid',
                'action': 'create',
                'content': '不正なトークン'
            })

            self.assertEqual(response.status_code, 200)
            self.assertIn('画面の有効期限が切れました'.encode(), response.data)
            self.assertEqual(app_module.instructions, [])
            save.assert_not_called()

            response = self.client.post('/board', data={
                'csrf_token': self.csrf_token(),
                'action': 'create',
                'content': '',
                'urgency': 'unexpected'
            })
            self.assertEqual(response.status_code, 200)
            self.assertIn('指示内容は1〜500文字'.encode(), response.data)
            self.assertEqual(app_module.instructions, [])
            save.assert_not_called()

    def test_instruction_storage_writes_the_complete_current_list(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = os.path.join(temporary_directory, 'instructions.json')
            expected = [{'id': 1, 'target': '住民', 'content': 'テスト', 'status': '有効'}]
            with (
                patch.object(app_module, 'INSTRUCTIONS_FILE', path),
                patch.object(app_module, 'instructions', expected)
            ):
                app_module.save_instructions()

            with open(path, encoding='utf-8') as stored_file:
                self.assertEqual(json.load(stored_file), expected)

    def test_released_instruction_is_removed_from_home(self):
        instruction = {
            'id': 1,
            'target': '住民',
            'content': '避難してください',
            'urgency': '通常',
            'status': '有効',
            'created_at': '2026年10月04日 12:00'
        }
        with (
            patch.object(app_module, 'instructions', [instruction]),
            patch.object(app_module, 'save_instructions'),
            patch.object(app_module, 'get_disaster_information', return_value=([], False)),
            patch.object(app_module, 'shelter_data_error', False)
        ):
            with self.client.session_transaction() as current_session:
                current_session['logged_in'] = True
            self.client.get('/board')
            response = self.client.post('/board', data={
                'csrf_token': self.csrf_token(),
                'action': 'update_status',
                'instruction_id': '1',
                'status': '解除'
            })

            self.assertEqual(response.status_code, 302)
            self.assertEqual(instruction['status'], '解除')
            home_response = self.client.get('/')

        self.assertNotIn('避難してください'.encode(), home_response.data)
        self.assertIn('現在、有効な住民向け指示はありません'.encode(), home_response.data)

    def test_weather_refresh_failure_discloses_cached_data_is_stale(self):
        cached = {
            'area_name': '青森市',
            'warnings': [{'name': '大雨警報', 'status': '発表'}],
            'report_time': '2026年10月04日 11:00',
            'last_fetch_time': '2026年10月04日 11:05'
        }
        with (
            patch.object(app_module, 'weather_warnings_cache', cached),
            patch.object(app_module.urllib.request, 'urlopen', side_effect=OSError('offline'))
        ):
            with self.assertLogs(app_module.app.logger, level='ERROR'):
                result = app_module.get_weather_warnings()

        self.assertTrue(result['error'])
        self.assertTrue(result['stale'])
        self.assertEqual(result['warnings'], cached['warnings'])
        self.assertIn('last_attempt_time', result)

    def test_weather_refresh_success_returns_fetch_and_report_times(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def read(self):
                return json.dumps([{
                    'reportDatetime': '2026-10-04T01:00:00+00:00',
                    'warning': {
                        'class20Items': [{
                            'areaCode': app_module.AREA_CODE,
                            'kinds': [{'status': '発表', 'code': '04'}]
                        }]
                    }
                }]).encode()

        with (
            patch.object(app_module, 'weather_warnings_cache', None),
            patch.object(app_module.urllib.request, 'urlopen', return_value=FakeResponse())
        ):
            result = app_module.get_weather_warnings()

        self.assertFalse(result['error'])
        self.assertFalse(result['stale'])
        self.assertEqual(result['warnings'][0]['name'], '洪水警報')
        self.assertIn('last_fetch_time', result)
        self.assertEqual(result['report_time'], '2026年10月04日 10:00')


if __name__ == '__main__':
    unittest.main()
