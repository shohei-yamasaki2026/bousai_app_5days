import unittest
from unittest.mock import patch

import app as app_module


class ShelterSearchTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config['TESTING'] = True
        self.client = app_module.app.test_client()
        self.shelters_patch = patch.object(
            app_module,
            'shelters',
            [
                {
                    'id': 1,
                    'name': '青葉小学校',
                    'district': '青葉区',
                    'disaster_types': ['tsunami', 'flood'],
                    'facilities': ['pets_allowed', 'barrier_free'],
                },
                {
                    'id': 2,
                    'name': '中央中学校',
                    'district': '中央区',
                    'disaster_types': ['flood'],
                    'facilities': ['pets_allowed'],
                },
                {'id': 3, 'name': '青葉コミュニティセンター'},
            ]
        )
        self.error_patch = patch.object(app_module, 'shelter_data_error', False)
        self.shelters_patch.start()
        self.error_patch.start()
        self.addCleanup(self.shelters_patch.stop)
        self.addCleanup(self.error_patch.stop)

    def test_search_page_shows_only_available_search_fields(self):
        response = self.client.get('/shelter_search')

        self.assertEqual(response.status_code, 200)
        self.assertIn('避難所名・地域名'.encode(), response.data)
        self.assertIn('青葉区'.encode(), response.data)
        self.assertIn('災害種別'.encode(), response.data)
        self.assertIn('ペット同伴'.encode(), response.data)
        self.assertIn('バリアフリー'.encode(), response.data)

    def test_search_page_clears_checked_conditions_on_reset(self):
        response = self.client.get(
            '/shelter_search?disaster_types=tsunami&facilities=pets_allowed'
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"form.addEventListener('reset'", response.data)
        self.assertIn(b'event.preventDefault()', response.data)
        self.assertIn(b'input.checked = false', response.data)

    def test_keyword_search_matches_name_and_district(self):
        response = self.client.get('/search_results?keyword=%E9%9D%92%E8%91%89')

        self.assertEqual(response.status_code, 200)
        self.assertIn('青葉小学校'.encode(), response.data)
        self.assertIn('青葉コミュニティセンター'.encode(), response.data)
        self.assertNotIn('中央中学校'.encode(), response.data)

    def test_keyword_and_district_are_combined(self):
        response = self.client.get(
            '/search_results?keyword=%E5%B0%8F%E5%AD%A6%E6%A0%A1&district=%E9%9D%92%E8%91%89%E5%8C%BA'
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('青葉小学校'.encode(), response.data)
        self.assertNotIn('中央中学校'.encode(), response.data)

    def test_disaster_and_facility_conditions_are_combined(self):
        response = self.client.get(
            '/search_results?disaster_types=tsunami&facilities=pets_allowed&facilities=barrier_free'
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('青葉小学校'.encode(), response.data)
        self.assertNotIn('中央中学校'.encode(), response.data)
        self.assertNotIn('青葉コミュニティセンター'.encode(), response.data)

    def test_multiple_selected_disasters_require_all_selected_conditions(self):
        response = self.client.get(
            '/search_results?disaster_types=tsunami&disaster_types=flood'
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('青葉小学校'.encode(), response.data)
        self.assertNotIn('中央中学校'.encode(), response.data)

    def test_missing_condition_metadata_does_not_match(self):
        response = self.client.get('/search_results?facilities=barrier_free')

        self.assertEqual(response.status_code, 200)
        self.assertIn('青葉小学校'.encode(), response.data)
        self.assertNotIn('中央中学校'.encode(), response.data)
        self.assertNotIn('青葉コミュニティセンター'.encode(), response.data)

    def test_back_to_search_preserves_conditions(self):
        response = self.client.get('/search_results?disaster_types=tsunami&facilities=pets_allowed')

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b'/shelter_search?disaster_types=tsunami&amp;facilities=pets_allowed',
            response.data
        )

    def test_search_without_matches_shows_recovery_guidance(self):
        response = self.client.get('/search_results?keyword=%E8%A9%B2%E5%BD%93%E3%81%AA%E3%81%97')

        self.assertEqual(response.status_code, 200)
        self.assertIn('条件に合う避難所が見つかりませんでした'.encode(), response.data)
        self.assertIn('検索画面に戻る'.encode(), response.data)

    def test_search_result_name_links_to_shelter_detail(self):
        response = self.client.get('/search_results?keyword=%E9%9D%92%E8%91%89')

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'href="/shelters/1?return_to=', response.data)
        self.assertIn('青葉小学校'.encode(), response.data)

    def test_search_results_compare_registered_shelter_information(self):
        with patch.object(
            app_module,
            'shelters',
            [{
                'id': 10,
                'name': '比較用避難所',
                'address': '青森市中央一丁目',
                'district': '中央区',
                'capacity': 100,
                'current_occupants': 70,
                'status': '開設中',
                'facilities': ['pets_allowed', 'barrier_free', 'wifi'],
                'disasters': ['earthquake', 'flood']
            }]
        ):
            response = self.client.get('/search_results')

        self.assertEqual(response.status_code, 200)
        for value in (
            '比較用避難所',
            '青森市中央一丁目',
            '開設中',
            '100人',
            '70人',
            '混雑',
            'ペット可',
            'バリアフリー',
            'Wi-Fi',
            '地震',
            '洪水'
        ):
            self.assertIn(value.encode(), response.data)
        self.assertIn('🐾'.encode(), response.data)
        self.assertIn('♿'.encode(), response.data)

    def test_crowding_thresholds_are_displayed_correctly(self):
        with patch.object(
            app_module,
            'shelters',
            [
                {'id': 11, 'name': '空きあり施設', 'capacity': 100, 'current_occupants': 69},
                {'id': 12, 'name': '混雑施設', 'capacity': 100, 'current_occupants': 70},
                {'id': 13, 'name': '満員施設', 'capacity': 100, 'current_occupants': 100}
            ]
        ):
            response = self.client.get('/search_results')

        self.assertEqual(response.status_code, 200)
        self.assertIn('空きあり施設'.encode(), response.data)
        self.assertIn('混雑施設'.encode(), response.data)
        self.assertIn('満員施設'.encode(), response.data)
        self.assertIn('is-available">空きあり'.encode(), response.data)
        self.assertIn('is-busy">混雑'.encode(), response.data)
        self.assertIn('is-full">満員'.encode(), response.data)

    def test_missing_comparison_fields_are_shown_as_unregistered(self):
        response = self.client.get('/search_results?keyword=%E9%9D%92%E8%91%89%E3%82%B3%E3%83%9F%E3%83%A5%E3%83%8B%E3%83%86%E3%82%A3')

        self.assertEqual(response.status_code, 200)
        self.assertIn('住所'.encode(), response.data)
        self.assertIn('収容人数'.encode(), response.data)
        self.assertIn('現在の避難者数'.encode(), response.data)
        self.assertIn('未登録'.encode(), response.data)
        self.assertNotIn('満員'.encode(), response.data)

    def test_empty_results_keep_search_and_home_navigation(self):
        response = self.client.get('/search_results?keyword=missing')

        self.assertEqual(response.status_code, 200)
        self.assertIn('条件に合う避難所がありません'.encode(), response.data)
        self.assertIn('検索画面に戻る'.encode(), response.data)
        self.assertIn('トップページへ戻る'.encode(), response.data)

    def test_detail_page_shows_registered_fields_and_marks_missing_data(self):
        response = self.client.get('/shelters/1')

        self.assertEqual(response.status_code, 200)
        self.assertIn('青葉小学校'.encode(), response.data)
        self.assertIn('青葉区'.encode(), response.data)
        self.assertIn('津波'.encode(), response.data)
        self.assertIn('洪水'.encode(), response.data)
        self.assertIn('ペット同伴'.encode(), response.data)
        self.assertIn('バリアフリー'.encode(), response.data)
        self.assertIn('住所'.encode(), response.data)
        self.assertIn('未登録'.encode(), response.data)
        self.assertIn('閉鎖中を意味しません'.encode(), response.data)

    def test_missing_disaster_and_facility_data_is_not_shown_as_negative(self):
        response = self.client.get('/shelters/3')

        self.assertEqual(response.status_code, 200)
        self.assertIn('未登録（非対応を意味しません）'.encode(), response.data)
        self.assertIn('未登録（利用不可を意味しません）'.encode(), response.data)

    def test_detail_page_returns_to_search_results_with_conditions(self):
        response = self.client.get(
            '/shelters/1?return_to=%2Fsearch_results%3Fkeyword%3Dtest%26facilities%3Dpets_allowed'
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'href="/search_results?keyword=test&amp;facilities=pets_allowed"', response.data)

    def test_detail_page_rejects_external_return_url(self):
        response = self.client.get(
            '/shelters/1?return_to=https%3A%2F%2Fevil.example%2F'
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'href="/shelter_search"', response.data)

    def test_unknown_shelter_id_returns_404(self):
        response = self.client.get('/shelters/999')

        self.assertEqual(response.status_code, 404)
        self.assertIn('避難所が見つかりません'.encode(), response.data)
        self.assertIn('検索結果に戻る'.encode(), response.data)

    def test_data_error_is_displayed_instead_of_no_results(self):
        with patch.object(app_module, 'shelter_data_error', True):
            response = self.client.get('/search_results')

        self.assertEqual(response.status_code, 200)
        self.assertIn('避難所データを読み込めませんでした'.encode(), response.data)
        self.assertNotIn('条件に合う避難所が見つかりませんでした'.encode(), response.data)

    def test_existing_home_page_remains_available(self):
        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertIn('避難所マップ'.encode(), response.data)


if __name__ == '__main__':
    unittest.main()
