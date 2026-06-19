from django.db import migrations


def update_tarot_decks(apps, schema_editor):
    TarotDeck = apps.get_model('tarot', 'TarotDeck')
    
    updates = {
        'Таро Романтическая Викториана / Victorian Romantic tarot': {
            'slug': 'victorian-romantic',
            'seo_tags': 'викторианская романтика, romantic, victorian, викториана',
        },
        'Таро волшебников': {
            'slug': 'tarot-volshebnikov',
            'seo_tags': 'волшебники, маги, магия, wizards',
        },
        'Ибис Таро / Ibis Tarot': {
            'slug': 'ibis',
            'seo_tags': 'ибис, птица, ibis, египет, egypt',
        },
        'Gilded tarot Чиро Марчетти': {
            'slug': 'gilded',
            'seo_tags': 'gilded, марчетти, marchetti, золотое, позолоченное',
        },
        'Золотое Таро Климта': {
            'slug': 'klimt',
            'seo_tags': 'климт, klimt, золотое, art nouveau, модерн',
        },
        'Кельтское Таро': {
            'slug': 'celtic',
            'seo_tags': 'кельтское, celtic, кельты, ирландия, узоры',
        },
        'Марсельское Таро': {
            'slug': 'marseille',
            'seo_tags': 'марсельское, marseille, классическое, французское',
        },
        'Мистическое Таро Мечтателя': {
            'slug': 'dreamer',
            'seo_tags': 'мечтатель, мистическое, dreamer, mystic',
        },
        'Мифологическое таро': {
            'slug': 'mythological',
            'seo_tags': 'мифология, mythological, греция, боги, myths',
        },
        'Пираты Карибского моря': {
            'slug': 'pirates',
            'seo_tags': 'пираты, pirates, карибское, caribbean, джек воробей',
        },
        'Сказочное Таро': {
            'slug': 'fairy-tale',
            'seo_tags': 'сказочное, сказки, fairy tale, сказка',
        },
        'Средневековое Таро': {
            'slug': 'medieval',
            'seo_tags': 'средневековое, medieval, средневековье, рыцари',
        },
        'Ступени Золотого Таро': {
            'slug': 'golden-stairs',
            'seo_tags': 'ступени, золотое, stairs, golden',
        },
        'Таро Безумной Луны': {
            'slug': 'crazy-moon',
            'seo_tags': 'безумная луна, луна, moon, crazy, лунное',
        },
        'Таро Белой и Черной магии': {
            'slug': 'white-black-magic',
            'seo_tags': 'белая магия, черная магия, white magic, black magic',
        },
        'Таро Босха': {
            'slug': 'bosch',
            'seo_tags': 'босх, bosch, иероним, средневековье, сюрреализм',
        },
        'Таро Ботичелли': {
            'slug': 'botticelli',
            'seo_tags': 'ботичелли, botticelli, ренессанс, возрождение',
        },
        'Таро Брейгеля': {
            'slug': 'bruegel',
            'seo_tags': 'брейгель, bruegel, нидерланды, средневековье',
        },
        'Ведьмовское Таро (Таро Ведьм) / Witchy Tarot': {
            'slug': 'witchy',
            'seo_tags': 'ведьмовское, ведьмы, witchy, witches, колдовство',
        },
        'Таро Висконти': {
            'slug': 'visconti',
            'seo_tags': 'висконти, visconti, историческое, средневековье, италия',
        },
        'Таро Гномов': {
            'slug': 'gnomes',
            'seo_tags': 'гномы, gnomes, гном, сказочное',
        },
        'Таро Джейн Остин': {
            'slug': 'jane-austen',
            'seo_tags': 'джейн остин, austen, викторианская, регентство, англия',
        },
        'Таро Драконов': {
            'slug': 'dragons',
            'seo_tags': 'драконы, dragons, фэнтези, fantasy',
        },
        'Ремесла друидов': {
            'slug': 'druid-craft',
            'seo_tags': 'друиды, druid, druids, кельтское, природа, ремесла',
        },
        'Таро Золотого рассвета': {
            'slug': 'golden-dawn',
            'seo_tags': 'золотой рассвет, golden dawn, герметическое, оккультное',
        },
        'Таро Золотой зари': {
            'slug': 'golden-dawn-rituals',
            'seo_tags': 'золотая заря, golden dawn, герметическое, ритуалы',
        },
        'Таро Иллюминатов': {
            'slug': 'illuminati',
            'seo_tags': 'иллюминаты, illuminati, современное, contemporary',
        },
        'Таро Колесо Года': {
            'slug': 'wheel-of-year',
            'seo_tags': 'колесо года, wheel of year, колесо, языческое, sabbat',
        },
        'Таро Ленорман': {
            'slug': 'lenormand',
            'seo_tags': 'ленорман, lenormand, гадание, оракул',
        },
        'Мир Леонардо Да Винчи': {
            'slug': 'da-vinci',
            'seo_tags': 'леонардо, да винчи, da vinci, ренессанс, итальянское',
        },
        'Таро Магических Символов': {
            'slug': 'magic-symbols',
            'seo_tags': 'магические символы, magic symbols, символы, знаки',
        },
        'Таро Магия Снов': {
            'slug': 'dream-magic',
            'seo_tags': 'магия снов, dream, сны, dreamlike',
        },
        'Таро Мистерии Авалона': {
            'slug': 'avalon',
            'seo_tags': 'авалон, avalon, артур, кельтское, мистерии',
        },
        'Таро Мона Лиза': {
            'slug': 'mona-lisa',
            'seo_tags': 'мона лиза, mona lisa, леонардо, ренессанс',
        },
        'Таро New Vision': {
            'slug': 'new-vision',
            'seo_tags': 'new vision, новое видение, оборотная сторона, reverse',
        },
        'Таро Ритуалы Ордена «Золотой Зари»': {
            'slug': 'golden-dawn-order',
            'seo_tags': 'золотая заря, golden dawn, ритуалы, ордена, герметическое',
        },
        'Герметическое таро': {
            'slug': 'hermetic',
            'seo_tags': 'герметическое, hermetic, оккультное, каббала, kabbalah',
        },
        'Таро Ошо Дзен': {
            'slug': 'osho-zen',
            'seo_tags': 'ошо, osho, дзен, zen, медитация, буддизм',
        },
        'Предсказательное таро Порог Вечности': {
            'slug': 'threshold-eternity',
            'seo_tags': 'порог вечности, предсказательное, eternity',
        },
        'Путешествие на Восток': {
            'slug': 'journey-east',
            'seo_tags': 'восток, путешествие, journey east, азия, oriental',
        },
        'Таро Ренессанс': {
            'slug': 'renaissance',
            'seo_tags': 'ренессанс, renaissance, возрождение, итальянское',
        },
        'Ренессанса Джейн Лайл': {
            'slug': 'renaissance-lyle',
            'seo_tags': 'лайл, lyle, ренессанс, renaissance',
        },
        'Таро Святого Грааля': {
            'slug': 'holy-grail',
            'seo_tags': 'грааль, святой грааль, holy grail, артур, рыцари',
        },
        'Таро Снов Чиро Марчетти': {
            'slug': 'dreams-marchetti',
            'seo_tags': 'сны, марчетти, dreams, marchetti, dreamers',
        },
        'Таро Союз Богинь': {
            'slug': 'goddess-union',
            'seo_tags': 'богини, goddess, союз, женское, feminine',
        },
        'Таро Тота Алистера Кроули': {
            'slug': 'thoth',
            'seo_tags': 'тот, thoth, кроули, crowley, герметическое, каббала',
        },
        'Таро Короля-Солнце (Таро Трех Мушкетеров) / I Tarocchi del Re Sole': {
            'slug': 'roi-soleil',
            'seo_tags': 'король солнце, три мушкетера, roi soleil, французское, барокко',
        },
        'Таро Тысяча и одна Ночь': {
            'slug': 'arabian-nights',
            'seo_tags': 'тысяча и одна ночь, arabian nights, восток, шехерезада',
        },
        'Универсальный ключ': {
            'slug': 'universal-key',
            'seo_tags': 'универсальный ключ, universal key, уэйт, waite',
        },
        'Таро Цветов': {
            'slug': 'flowers',
            'seo_tags': 'цветы, flowers, botanical, ботаническое, природа',
        },
        'Таро Эльфов': {
            'slug': 'elves',
            'seo_tags': 'эльфы, elves, фэнтези, fantasy, фэйри',
        },
        'Таро Эра Водолея': {
            'slug': 'aquarian',
            'seo_tags': 'водолей, aquarian, era, эра, новый век',
        },
        'Золотое Флорентийское Таро': {
            'slug': 'florentine',
            'seo_tags': 'флоренция, флорентийское, florentine, золотое, ренессанс',
        },
        'Цыганское Таро Бакленда / Buckland Romani Tarot': {
            'slug': 'buckland-romani',
            'seo_tags': 'цыганское, buckland, romani, цыгане, gypsy',
        },
        'Star Wars Taro': {
            'slug': 'star-wars',
            'seo_tags': 'star wars, звездные войны, джедай, jedi, кино, фантастика',
        },
        'Rider-Waite-Smith Tarot': {
            'slug': 'waite',
            'seo_tags': 'уэйт, waite, rider, классическое, стандартное, rws',
        },
        'Таро Белых Кошек': {
            'slug': 'white-cats',
            'seo_tags': 'белые кошки, white cats, кошки, cats',
        },
        'The Urban Tarot (U.S. Games Systems)': {
            'slug': 'urban',
            'seo_tags': 'urban, городское, современное, city, урбан',
        },
        'The Somnia Tarot': {
            'slug': 'somnia',
            'seo_tags': 'somnia, сны, dreams, темное, dark',
        },
        'Таро 78 Волшебников / Sorcerers Tarot': {
            'slug': '78-sorcerers',
            'seo_tags': '78 волшебников, sorcerers, маги',
        },
        'Таро Агни Рерихов': {
            'slug': 'agni-roerich',
            'seo_tags': 'агни, рерих, roerich, индия, эзотерика',
        },
        'The Fountain Tarot': {
            'slug': 'fountain',
            'seo_tags': 'fountain, фонтан, современное, минимализм, minimal',
        },
        'Таро Забытых Легенд / Tarot of the Forgotten Legends': {
            'slug': 'forgotten-legends',
            'seo_tags': 'забытые легенды, forgotten legends, мифология',
        },
        'Таро Таинственного леса': {
            'slug': 'mystic-forest',
            'seo_tags': 'таинственный лес, mystic forest, природа, лес',
        },
        'Золотое Таро Уэйт Арт-Нуво / Golden Art Nouveau Tarot': {
            'slug': 'golden-art-nouveau',
            'seo_tags': 'арт нуво, art nouveau, уэйт, waite, золотое, модерн',
        },
        'Supernatural Tarot': {
            'slug': 'supernatural',
            'seo_tags': 'supernatural, сверхъестественное, сериал, dean, sam',
        },
        'Таро Темного леса / Darkwood Tarot': {
            'slug': 'darkwood',
            'seo_tags': 'темный лес, darkwood, dark, мрачное',
        },
        'Таро Ведьма Каждый День / Everyday Witch Tarot': {
            'slug': 'everyday-witch',
            'seo_tags': 'everyday witch, ведьма каждый день, ведьмы, практичное',
        },
        'Manara': {
            'slug': 'manara',
            'seo_tags': 'манара, manara, эротическое, erotic, комикс, comics',
        },
        'Таро Теневого Света / Shadow Light Tarot': {
            'slug': 'shadow-light',
            'seo_tags': 'теневой свет, shadow light, тень, shadow',
        },
        'Таро Русалок / Mermaid Tarot': {
            'slug': 'mermaid',
            'seo_tags': 'русалки, mermaid, море, ocean, water',
        },
        'Omegaland Tarot': {
            'slug': 'omegaland',
            'seo_tags': 'omegaland, постапокалипсис, мрачное, dark',
        },
        'Gay Tarot': {
            'slug': 'gay',
            'seo_tags': 'gay, лгбт, lgbt, квир, queer',
        },
        'Таро Сумасшедшего Дома / Madhouse Tarot': {
            'slug': 'madhouse',
            'seo_tags': 'сумасшедший дом, madhouse, безумие, психиатрия',
        },
        'Интуитивное таро Ночной Богини / The Intuitive Night Goddess Tarot': {
            'slug': 'night-goddess',
            'seo_tags': 'ночная богиня, night goddess, интуитивное, intuitive',
        },
        'Черный Гримуар': {
            'slug': 'black-grimoire',
            'seo_tags': 'черный гримуар, grimoire, темное, dark, магия',
        },
        'Таро Николетты Чеколли': {
            'slug': 'ceccoli',
            'seo_tags': 'чеколли, ceccoli, николетта, иллюстрация, cute',
        },
        'Так и внизу (Книга Теней Том 2)': {
            'slug': 'as-above-so-below',
            'seo_tags': 'книга теней, as above so below, так вверху, так внизу',
        },
        '78 дверей': {
            'slug': '78-doors',
            'seo_tags': '78 дверей, 78 doors, двери, символизм',
        },
        'Игра Престолов': {
            'slug': 'game-of-thrones',
            'seo_tags': 'игра престолов, game of thrones, вестерос, дракарис',
        },
        'Таро Светлого Провидца': {
            'slug': 'bright-seer',
            'seo_tags': 'светлый провидец, bright, светлое, провидение',
        },
        'Таро Злодеев Диснея / Disney Villains Tarot': {
            'slug': 'disney-villains',
            'seo_tags': 'дисней, disney, злодеи, villains, мультфильмы',
        },
        'Таро Черных Котов / Black Cats Tarot': {
            'slug': 'black-cats',
            'seo_tags': 'черные кошки, black cats, кошки, cats',
        },
        'Таро Золотого Колеса / Tarot of the Golden Wheel': {
            'slug': 'golden-wheel',
            'seo_tags': 'золотое колесо, golden wheel, колесо фортуны',
        },
        'Таро Вуду Нового Орлеана / New Orleans Voodoo Tarot': {
            'slug': 'voodoo',
            'seo_tags': 'вуду, voodoo, новый орлеан, new orleans, магия',
        },
        'Neon Moon': {
            'slug': 'neon-moon',
            'seo_tags': 'neon moon, неон, neon, луна, moon, современное',
        },
        'Таро Викторианских Фей / Victorian Fairy Tarot': {
            'slug': 'victorian-fairy',
            'seo_tags': 'викторианские феи, victorian fairy, феи, fairies',
        },
        'Языческое таро': {
            'slug': 'pagan',
            'seo_tags': 'языческое, pagan, язычество, природа, sabbat',
        },
        'Таро Лунатиков': {
            'slug': 'lunatics',
            'seo_tags': 'лунатики, lunatics, луна, moon, безумие',
        },
        "Third Eye Tarot (Britt's Art)": {
            'slug': 'third-eye',
            'seo_tags': 'третий глаз, third eye, интуиция, психоделика',
        },
        'Dragon Age Tarot': {
            'slug': 'dragon-age',
            'seo_tags': 'dragon age, игра, game, фэнтези, bioware',
        },
        'MIMIT | Mini Mice Tarot': {
            'slug': 'mini-mice',
            'seo_tags': 'мышки, mice, kawaii, милое, cute, миниатюрное',
        },
        'Таро Манара': {
            'slug': 'manara-extended',
            'seo_tags': 'манара, manara, эротическое, erotic, расширенная',
        },
    }
    
    for name, data in updates.items():
        TarotDeck.objects.filter(name=name).update(
            slug=data['slug'],
            seo_tags=data['seo_tags'],
        )


def reverse_update(apps, schema_editor):
    # Обнуляем slug и seo_tags обратно
    TarotDeck = apps.get_model('tarot', 'TarotDeck')
    TarotDeck.objects.all().update(slug=None, seo_tags=None)


class Migration(migrations.Migration):
    dependencies = [
        ('tarot', '0018_tarotdeck_seo_tags_tarotdeck_slug_and_more'),  # замени на последнюю миграцию
    ]

    operations = [
        migrations.RunPython(update_tarot_decks, reverse_update),
    ]