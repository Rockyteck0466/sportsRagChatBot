from app.content_cleaner import clean_markdown


def test_about_page_keeps_faq_and_removes_navigation_and_footer() -> None:
    markdown = """---
url: https://www.nba.com/news/about
---
* [Games](/games)
* [Teams](/teams)

### News Archive

# About The NBA

Learn more about the NBA and frequently asked questions.

# About the NBA

**How many teams are there in the NBA?**

The NBA is a collection of 30 teams, broken down by two conferences.

### Related

This recommendation must not be indexed.
"""

    cleaned = clean_markdown(markdown)

    assert cleaned.startswith("# About The NBA")
    assert "**How many teams are there in the NBA?**" in cleaned
    assert "The NBA is a collection of 30 teams" in cleaned
    assert "Games" not in cleaned
    assert "News Archive" not in cleaned
    assert "This recommendation must not be indexed." not in cleaned


def test_official_rule_page_keeps_rule_and_removes_site_boilerplate() -> None:
    markdown = """# [ NBA Official
](https://official.nba.com/)

* [RULE NO. 1: Court Dimensions](https://official.nba.com/rule-no-1/)
* [RULE NO. 2: Officials](https://official.nba.com/rule-no-2/)

# RULE NO 3: Players, Substitutes and Coaches

Section I\u00e2\u20ac\u201dTeam

1. Each team shall consist of five players.

* [Today's Officials](https://official.nba.com/referee-assignments/)
* [Shop](https://store.nba.com/)
"""

    cleaned = clean_markdown(markdown)

    assert cleaned.startswith("# RULE NO 3: Players, Substitutes and Coaches")
    assert "Section I\u2014Team" in cleaned
    assert "\u00e2\u20ac\u201d" not in cleaned
    assert "Each team shall consist of five players." in cleaned
    assert "Court Dimensions" not in cleaned
    assert "Today's Officials" not in cleaned
    assert "Shop" not in cleaned


def test_mixed_mojibake_player_names_are_repaired() -> None:
    markdown = """# ROSTER

Nikola VuÄ\u008deviÄ\u0087
VÃ\u00adt KrejÄ\u008dÃ\u00ad
Hugo GonzÃ\u00a1lez
"""

    cleaned = clean_markdown(markdown)

    assert "Nikola Vučević" in cleaned
    assert "Vít Krejčí" in cleaned
    assert "Hugo González" in cleaned
