import unittest
from unittest.mock import patch

import app as app_module


class BoardAndShelterManagementTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config['TESTING'] = True
        self.client = app_module.app.test_client()
        with self.client.session_transaction() as current_session:
            current_session['logged_in'] = True

    def csrf_token(self):
        self.client.get('/board')
        with self.client.session_transaction() as current_session:
            return current_session['_csrf_token']

    def test_board_has_separate_instruction_and_announcement_forms(self):
        response = self.client.get('/board')

        self.assertEqual(response.status_code, 200)
        self.assertIn('災害対応の指示を登録'.encode(), response.data)
        self.assertIn('住民向けに発信'.encode(), response.data)
        self.assertIn('ホーム画面に表示する'.encode(), response.data)
        self.assertIn('標準'.encode(), response.data)
        self.assertIn('やさしい日本語'.encode(), response.data)

    def test_instruction_creation_saves_required_metadata_and_confirmation(self):
        with (
            patch.object(app_module, 'instructions', []),
            patch.object(app_module, 'save_instructions') as save
        ):
            response = self.client.post('/board', data={
                'csrf_token': self.csrf_token(),
                'action': 'create_instruction',
                'target': '住民',
                'target_area': '中央地区',
                'recipient': '住民向け',
                'priority': '高',
                'content': '安全な場所へ避難してください',
                'shelter': '中央小学校'
            })

            self.assertEqual(response.status_code, 302)
            record = app_module.instructions[0]
            self.assertEqual(record['target'], '住民')
            self.assertEqual(record['target_area'], '中央地区')
            self.assertEqual(record['recipient'], '住民向け')
            self.assertEqual(record['priority'], '高')
            self.assertEqual(record['status'], '未対応')
            self.assertEqual(record['shelter'], '中央小学校')
            save.assert_called_once()

            response = self.client.get(response.location)
            self.assertIn('window.confirm'.encode(), response.data)
            self.assertIn('内容：'.encode(), response.data)

    def test_instruction_validation_preserves_form_values(self):
        with patch.object(app_module, 'instructions', []), patch.object(app_module, 'save_instructions') as save:
            response = self.client.post('/board', data={
                'csrf_token': self.csrf_token(),
                'action': 'create_instruction',
                'target': '住民',
                'target_area': '北地区',
                'recipient': '住民向け',
                'priority': '超高',
                'content': '',
                'shelter': ''
            })

            self.assertEqual(response.status_code, 200)
            self.assertIn('指示内容は1〜2000文字'.encode(), response.data)
            self.assertIn('北地区'.encode(), response.data)
            self.assertEqual(app_module.instructions, [])
            save.assert_not_called()

    def test_announcement_home_visibility_is_respected_and_persisted(self):
        with (
            patch.object(app_module, 'instructions', []),
            patch.object(app_module, 'save_instructions') as save,
            patch.object(app_module, 'get_disaster_information', return_value=([], False)),
            patch.object(app_module, 'shelter_data_error', False),
            patch.object(app_module, 'shelters', [])
        ):
            response = self.client.post('/board', data={
                'csrf_token': self.csrf_token(),
                'action': 'create_announcement',
                'recipient': '住民向け',
                'target_area': '東地区',
                'content': '給水所を開設しました',
                'display_on_home': 'on'
            })

            self.assertEqual(response.status_code, 302)
            announcement = app_module.instructions[0]
            self.assertEqual(announcement['kind'], 'announcement')
            self.assertIs(announcement['display_on_home'], True)
            save.assert_called_once()

            home = self.client.get('/')
            self.assertIn('給水所を開設しました'.encode(), home.data)
            self.assertIn('東地区'.encode(), home.data)

            app_module.instructions.append({
                **announcement,
                'id': 2,
                'content': 'ホーム非表示の記録',
                'display_on_home': False
            })
            home = self.client.get('/')
            self.assertNotIn('ホーム非表示の記録'.encode(), home.data)

    def test_instruction_status_change_persists(self):
        record = {
            'id': 40,
            'kind': 'instruction',
            'target': '道路管理担当部署',
            'content': '通行止めを確認',
            'priority': '中',
            'status': '未対応',
            'created_at': '2026年10月04日 12:00'
        }
        with patch.object(app_module, 'instructions', [record]), patch.object(app_module, 'save_instructions') as save:
            response = self.client.post('/board', data={
                'csrf_token': self.csrf_token(),
                'action': 'update_instruction_status',
                'instruction_id': '40',
                'status': '対応中'
            })

            self.assertEqual(response.status_code, 302)
            self.assertEqual(record['status'], '対応中')
            self.assertIn('updated_at', record)
            save.assert_called_once()

    def test_instruction_list_supports_recent_and_priority_sorting(self):
        records = [
            {
                'id': 1,
                'kind': 'instruction',
                'target': '住民',
                'content': '古い高優先度',
                'priority': '高',
                'status': '未対応',
                'created_at': '2026年10月04日 10:00',
                'created_at_iso': '2026-10-04T10:00:00+09:00'
            },
            {
                'id': 2,
                'kind': 'instruction',
                'target': '住民',
                'content': '新しい低優先度',
                'priority': '低',
                'status': '未対応',
                'created_at': '2026年10月04日 12:00',
                'created_at_iso': '2026-10-04T12:00:00+09:00'
            }
        ]
        with patch.object(app_module, 'instructions', records):
            recent = self.client.get('/board?sort=recent')
            priority = self.client.get('/board?sort=priority')

        self.assertLess(recent.data.index('新しい低優先度'.encode()), recent.data.index('古い高優先度'.encode()))
        self.assertLess(priority.data.index('古い高優先度'.encode()), priority.data.index('新しい低優先度'.encode()))

    def test_shelter_registration_rejects_full_width_digits_and_retains_input(self):
        with patch.object(app_module, 'shelters', []), patch.object(app_module, 'save_shelters') as save:
            response = self.client.post('/shelter_register', data={
                'csrf_token': self.csrf_token(),
                'name': '中央小学校',
                'address': '青森市中央一丁目',
                'capacity': '３００',
                'disasters': ['earthquake'],
                'facilities': ['wifi'],
                'status': '開設中'
            })

            self.assertEqual(response.status_code, 400)
            self.assertIn('半角数字'.encode(), response.data)
            self.assertIn('value="３００"'.encode(), response.data)
            self.assertIn('value="earthquake" checked'.encode(), response.data)
            self.assertEqual(app_module.shelters, [])
            save.assert_not_called()

    def test_shelter_create_and_edit_update_existing_record(self):
        records = [{'id': 8, 'name': '旧施設', 'custom_data': '保持'}]
        with patch.object(app_module, 'shelters', records), patch.object(app_module, 'save_shelters') as save:
            response = self.client.post('/shelter_register', data={
                'csrf_token': self.csrf_token(),
                'name': '中央小学校',
                'address': '青森市中央一丁目',
                'district': '中央区',
                'capacity': '300',
                'disasters': ['earthquake', 'flood'],
                'facilities': ['wifi'],
                'status': '開設中'
            })
            self.assertEqual(response.status_code, 302)
            self.assertEqual(len(records), 2)
            new_record = records[1]
            self.assertEqual(new_record['capacity'], 300)
            self.assertEqual(new_record['disasters'], ['earthquake', 'flood'])
            self.assertEqual(new_record['disaster_types'], ['earthquake', 'flood'])
            self.assertEqual(new_record['opening_status'], '開設中')
            response = self.client.get('/shelter_register?edit=8')
            self.assertEqual(response.status_code, 200)
            self.assertIn('旧施設'.encode(), response.data)

            response = self.client.post('/shelter_register?edit=8', data={
                'csrf_token': self.csrf_token(),
                'edit_id': '8',
                'name': '更新施設',
                'address': '青森市新町',
                'district': '新町',
                'capacity': '120',
                'disasters': ['tsunami'],
                'facilities': ['pets_allowed', 'barrier_free'],
                'status': '未開設'
            })

            self.assertEqual(response.status_code, 302)
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]['name'], '更新施設')
            self.assertEqual(records[0]['custom_data'], '保持')
            self.assertEqual(records[0]['capacity'], 120)
            self.assertEqual(records[0]['disasters'], ['tsunami'])
            save.assert_any_call()

    def test_shelter_form_requires_csrf_and_missing_edit_target_returns_404(self):
        with patch.object(app_module, 'shelters', []):
            missing = self.client.get('/shelter_register?edit=999')
            self.assertEqual(missing.status_code, 404)
            self.assertIn('編集する避難所が見つかりません'.encode(), missing.data)

            response = self.client.post('/shelter_register', data={
                'name': '中央小学校',
                'address': '青森市中央',
                'capacity': '100',
                'status': '開設中'
            })
            self.assertEqual(response.status_code, 400)
            self.assertIn('画面の有効期限が切れました'.encode(), response.data)

    def test_shelter_management_page_handles_legacy_fields(self):
        legacy = [{'id': 1, 'name': '旧施設'}]
        with patch.object(app_module, 'shelters', legacy):
            response = self.client.get('/shelter_register')

        self.assertEqual(response.status_code, 200)
        self.assertIn('旧施設'.encode(), response.data)
        self.assertIn('住所：未登録'.encode(), response.data)
        self.assertIn('収容人数：未登録'.encode(), response.data)
        self.assertIn('開設状況：未登録'.encode(), response.data)

    def test_shelter_save_error_rolls_back_memory_update(self):
        records = [{'id': 1, 'name': '旧施設'}]
        with (
            patch.object(app_module, 'shelters', records),
            patch.object(app_module, 'save_shelters', side_effect=OSError('disk full'))
        ):
            response = self.client.post('/shelter_register', data={
                'csrf_token': self.csrf_token(),
                'name': '中央小学校',
                'address': '青森市中央',
                'capacity': '100',
                'status': '開設中'
            })

        self.assertEqual(response.status_code, 500)
        self.assertEqual(records, [{'id': 1, 'name': '旧施設'}])
        self.assertIn('保存できませんでした'.encode(), response.data)


if __name__ == '__main__':
    unittest.main()
