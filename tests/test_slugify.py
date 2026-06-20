import re
import pytest
from src.app import slugify


class TestSlugify:
    def test_basic_scientific_name(self):
        assert slugify("Bombycilla cedrorum") == "bombycilla-cedrorum"

    def test_three_word_name(self):
        assert slugify("Corvus brachyrhynchos") == "corvus-brachyrhynchos"

    def test_already_lowercase(self):
        assert slugify("anas crecca") == "anas-crecca"

    def test_multiple_spaces(self):
        assert slugify("Turdus  migratorius") == "turdus-migratorius"

    def test_leading_trailing_spaces(self):
        assert slugify("  Corvus corax  ") == "corvus-corax"

    def test_hyphenated_species_name(self):
        assert slugify("Passer domesticus") == "passer-domesticus"

    def test_single_word(self):
        assert slugify("Animal") == "animal"

    def test_empty_string(self):
        assert slugify("") == ""

    def test_special_characters_stripped(self):
        assert slugify("Test!@#$%^&*()Name") == "test-name"

    def test_underscores_replaced(self):
        assert slugify("some_species_name") == "some-species-name"

    def test_numbers_preserved(self):
        assert slugify("Species 123 test") == "species-123-test"