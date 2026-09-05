"""Reviewed source-language primers for exact lesson identities.

See docs/lesson-errata.md for evidence, amendments and maintenance limits.
Canonical extracts intentionally replace stochastic paraphrases for these two
sources only; sentence regexes cannot guarantee removal of a source error.
"""

_HISTORY = """Bu dars qadimgi karvon savdo yo‘llari, ularning ahamiyati va xalqaro aloqalar tarixini yoritadi.

## Concepts & terms
- Savdo yo‘llari — turli mamlakatlar o‘rtasida tovar ayirboshlash va aloqa o‘rnatishga xizmat qiladigan qadimiy yo‘llar
- Karvonsaroy — savdo karvonlari to‘xtab o‘tadigan va tunaydigan maxsus binolar

## Worked-example types
- Qadimgi savdo yo‘llarining yo‘nalishlari va ulardan o‘tgan hududlarni tavsiflash
- «La’l yo‘li», «Shoh yo‘li» va Buyuk Ipak yo‘lining o‘xshash hamda farqli jihatlarini taqqoslash
- Savdo yo‘llarining davlatlar iqtisodiyoti va madaniy aloqalariga ta’sirini tushuntirish

## Key facts
- «La’l yo‘li» miloddan avvalgi $3-2$-ming yilliklarda ochilgan bo‘lib, Pomir tog‘idan boshlanib Eron, Mesopotamiya va Misr orqali o‘tgan
- «Shoh yo‘li» Eron shohi Doro I tomonidan tuzilgan. Darslikda uning ikki yo‘nalishi berilgan: biri O‘rtayer dengizi bo‘yidagi shaharlarni Eron bilan bog‘lagan; ikkinchisi Eron va Baqtriya orqali Oltoy va Hindistonga borgan
- Buyuk Ipak yo‘li miloddan avvalgi II asrda ochilgan. Darslikda uning uzunligi $12000$ km, xizmat qilgan davri esa o‘n yetti asr deb berilgan
- Buyuk Ipak yo‘li Xitoyning Sian shahridan boshlangan

## Vocabulary & set phrases
- La’l yo‘li — Pomirdan qimmatbaho toshlar tashilgan qadimgi savdo yo‘li
- Shoh yo‘li — Eron shohlari nazorat qilgan ikki yo‘nalishli qadimgi savdo yo‘li
- Buyuk Ipak yo‘li — Xitoyni Yevropa va Osiyo bilan bog‘lagan, asosan ipak savdosiga xizmat qilgan yirik xalqaro savdo yo‘li

## Source sentences & passages
- Yo‘lning «La’l yo‘li» deb atalishiga bu yo‘ldan qimmatbaho la’l toshining tashilishi sabab bo‘lgan.
- «Buyuk» so‘zi yo‘lning juda uzun bo‘lganligini hamda juda ko‘p xalqlar taqdiriga aloqador bo‘lganligini anglatadi, «ipak» so‘zi esa yo‘lning, asosan, ipak savdosiga xizmat qilganligini bildiradi."""

_TECHNOLOGY = """Технология севооборота — это практика поочередного выращивания различных видов сельскохозяйственных культур на одной и той же территории в течение нескольких вегетационных сезонов для улучшения свойств почвы и снижения вредителей.

## Concepts & terms
- Севооборот — практика выращивания ряда различных видов сельскохозяйственных культур на одной и той же территории в течение нескольких вегетационных сезонов
- Система севооборота — перечень культур, входящих в севооборот, или соотношение полей, занимаемых этими культурами, между собой
- Ротация — время, необходимое каждой культуре, внесённой в список, для посадки, совершив один полный оборот по всем полям в плане
- Культура-предшественник — культура, посаженная в севообороте перед текущей культурой
- Сплошной посев — выращивание одной культуры на одном поле в течение многих лет
- Монокультура — непрерывное выращивание одной и той же культуры на одном поле в течение многих лет

## Key facts
- Севооборот уменьшает зависимость от одного набора питательных веществ и вероятность развития устойчивых вредителей, сорняков и болезней
- При посадке люцерны на 2-3 года в почве накапливается большое количество органических остатков, восстанавливая структуру почвы
- Период ротации севооборота равен количеству полей в севообороте
- Посадка в поздние сроки приводит к снижению урожайности на 10-40% или гибели растения из-за усиления болезней"""

_REVIEWED = {
    ("history", "768820b7-54ea-45d2-bbb4-d95275ef95e6"): _HISTORY,
    ("texnologiya", "d93f33a7-8120-4895-bc51-d2055c8ef7d4"): _TECHNOLOGY,
}


def apply_lesson_errata(output_md: str, *, section_id: str, subject: str) -> str:
    """Return a reviewed primer for an exact source; otherwise preserve bytes."""
    return _REVIEWED.get((subject, section_id), output_md)
