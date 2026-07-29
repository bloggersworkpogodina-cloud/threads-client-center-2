from __future__ import annotations

import hashlib
import html
import os
import tempfile
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from billing import period, fmt


TEMPLATE_VERSION = "2026-07-28-v1"
POLICY_VERSION = "2026-07-28-v1"


def _font_path() -> str:
    bundled = Path(__file__).resolve().parent / "fonts" / "DejaVuSans.ttf"
    candidates = [
        str(bundled),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    raise RuntimeError("Не найден шрифт для PDF.")


def _register_font():
    if "ContractSans" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("ContractSans", _font_path()))


def _styles():
    _register_font()
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "BodyRU", parent=styles["BodyText"], fontName="ContractSans",
        fontSize=9.5, leading=13, spaceAfter=5 * mm,
    )
    small = ParagraphStyle(
        "SmallRU", parent=body, fontSize=8.2, leading=11,
    )
    title = ParagraphStyle(
        "TitleRU", parent=body, fontSize=15, leading=19,
        alignment=TA_CENTER, spaceAfter=6 * mm,
    )
    h = ParagraphStyle(
        "HeadingRU", parent=body, fontSize=11, leading=14,
        spaceBefore=3 * mm, spaceAfter=2 * mm,
    )
    return body, small, title, h


def money(price: int | None) -> str:
    return f"{int(price or 0):,}".replace(",", " ") + " руб."


def contract_version(client, signer_name: str, settings) -> str:
    payload = "|".join([
        TEMPLATE_VERSION,
        str(client["id"]),
        signer_name.strip(),
        client["services"] or "",
        str(client["service_price"] or 0),
        client["billing_start"] or "",
        settings.executor_name,
        settings.executor_inn,
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def policy_version(settings) -> str:
    payload = "|".join([
        POLICY_VERSION, settings.executor_name, settings.executor_inn,
        settings.executor_email,
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _p(text: str, style) -> Paragraph:
    return Paragraph(html.escape(text).replace("\n", "<br/>"), style)


def generate_contract_pdf(client, signer_name: str, settings, output_path: str, draft: bool = False) -> str:
    body, small, title, h = _styles()
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title="Договор оказания услуг по продвижению в Threads",
        author=settings.executor_name,
    )
    if not client["billing_start"]:
        raise ValueError("Не указана дата начала расчётного периода")
    ps, pe, due = period(client["billing_start"], 0)
    ns, ne, ndue = period(client["billing_start"], 1)
    story = []
    label = "ЧЕРНОВИК - ДЛЯ ПРОВЕРКИ" if draft else "ДОГОВОР ОКАЗАНИЯ УСЛУГ ПО ПРОДВИЖЕНИЮ В THREADS"
    story.append(_p(label, title))

    contract_no = f"TCC-{client['id']}-{datetime.now().strftime('%Y%m%d')}"
    story.append(_p(
        f"Договор N {contract_no}\nДата формирования: {datetime.now().strftime('%d.%m.%Y')}",
        small,
    ))

    story.append(_p("1. Стороны и предмет договора", h))
    story.append(_p(
        f"{settings.executor_name}, ИНН {settings.executor_inn}, далее - Исполнитель, "
        f"и {signer_name}, далее - Заказчик, заключают настоящий договор об оказании услуг "
        f"по продвижению и сопровождению проекта в социальной сети Threads.",
        body,
    ))

    story.append(_p("2. Расчётный период и услуги", h))
    story.append(_p(
        f"Первый расчётный период: с {fmt(ps)} по {fmt(pe)}. Следующий расчётный период: с {fmt(ns)} по {fmt(ne)}.\n\nИсполнитель оказывает Заказчику следующие услуги:\n{client['services'] or 'Услуги не указаны.'}",
        body,
    ))
    story.append(_p(
        "Конкретный формат, календарь публикаций, материалы, согласования и рабочие задачи "
        "могут уточняться сторонами в Telegram и в подключенных к проекту рабочих материалах.",
        body,
    ))

    story.append(_p("3. Стоимость и расчеты", h))
    story.append(_p(
        f"Стоимость услуг составляет {money(client['service_price'])} за один расчётный период. "
        f"Оплата первого расчётного периода производится авансом не позднее {fmt(due)}. "
        f"Оплата следующего расчётного периода производится не позднее {fmt(ndue)}. "
        "В дальнейшем оплата производится авансом не позднее чем за 7 календарных дней до начала каждого следующего расчётного периода. "
        "Если оплата не поступила в установленный срок, Исполнитель вправе не приступать к новому расчётному периоду.",
        body,
    ))

    story.append(_p("4. Подготовка контента и участие Заказчика", h))
    story.append(_p(
        "4.1. Перечнем услуг предусмотрено создание ежедневного контента. Исполнитель осуществляет подготовку контента регулярно в течение оплаченного расчётного периода ежедневно, за исключением субботы.",
        body,
    ))
    story.append(_p(
        "4.2. Для подготовки отдельных публикаций Исполнитель вправе запрашивать у Заказчика необходимую информацию, материалы, комментарии, доступы и согласования. Заказчик обязуется предоставлять их в срок, позволяющий соблюдать график.",
        body,
    ))
    story.append(_p(
        "4.3. Если подготовка или публикация контента невозможна из-за того, что Заказчик своевременно не предоставил необходимые сведения, материалы, доступы или согласование, отсутствие публикации в соответствующий день не признаётся нарушением обязательств Исполнителем. Такой день входит в оплаченный расчётный период и не является основанием для уменьшения стоимости услуг или продления расчётного периода.",
        body,
    ))
    story.append(_p(
        "4.4. Исполнитель вправе заменить запланированную публикацию другим материалом, если это возможно без ущерба для качества и достоверности контента, но не обязан делать такую замену, если без информации Заказчика невозможно обеспечить необходимое качество.",
        body,
    ))
    story.append(_p(
        "4.5. Задержка Заказчиком согласования уже подготовленного материала не считается нарушением сроков со стороны Исполнителя.",
        body,
    ))
    story.append(_p(
        "Исполнитель самостоятельно организует процесс оказания услуг в пределах согласованного объема. "
        "Заказчик своевременно предоставляет исходные данные, доступы, материалы и обратную связь, "
        "которые необходимы для работы.",
        body,
    ))
    story.append(_p(
        "Исполнитель не гарантирует конкретное количество просмотров, подписчиков, заявок, продаж "
        "или иной коммерческий результат, поскольку такие показатели зависят в том числе от алгоритмов "
        "платформ, продукта Заказчика, спроса, действий аудитории и решений самого Заказчика.",
        body,
    ))

    story.append(_p("5. Материалы и права", h))
    story.append(_p(
        "Заказчик подтверждает наличие прав и разрешений на материалы, которые передает Исполнителю. "
        "После полной оплаты соответствующего периода Заказчик вправе использовать созданные для него "
        "тексты и иные итоговые материалы в своем проекте без ограничения территории и срока, "
        "если стороны отдельно не согласовали иное.",
        body,
    ))

    story.append(_p("6. Срок, отказ от договора и возврат", h))
    story.append(_p(
        "Договор действует с момента его акцепта Заказчиком в Telegram-боте и до прекращения сторонами. Услуги оказываются расчётными периодами. "
        "Заказчик вправе отказаться от исполнения договора, уведомив Исполнителя через Telegram либо по электронной почте. "
        "При прекращении договора расчёты производятся с учётом объёма фактически оказанных услуг и применимых требований законодательства Российской Федерации. "
        "Если Заказчик является потребителем, возврат производится с учётом законодательства о защите прав потребителей, включая фактически понесённые Исполнителем расходы. "
        "Если Заказчик не планирует продолжать сотрудничество в следующем расчётном периоде, он вправе не оплачивать следующий период. "
        "Отсутствие публикаций по причине непредоставления Заказчиком необходимых данных не является основанием для уменьшения стоимости соответствующего периода.",
        body,
    ))

    story.append(_p("7. Электронное взаимодействие", h))
    story.append(_p(
        "Стороны признают юридически значимыми сообщения и действия, совершенные через Telegram-аккаунты, "
        "используемые ими при работе с проектом. Акцептом настоящего договора является нажатие Заказчиком "
        "кнопки подтверждения договора в Telegram-боте после получения текста договора. "
        "Бот фиксирует Telegram ID, username, ФИО, дату и время подтверждения, а также версию документа.",
        body,
    ))

    story.append(_p("8. Персональные данные", h))
    story.append(_p(
        "Обработка персональных данных осуществляется отдельно в соответствии с Политикой обработки "
        "персональных данных. Согласие на обработку персональных данных запрашивается у Заказчика "
        "отдельным действием в Telegram-боте.",
        body,
    ))

    story.append(_p("9. Реквизиты Исполнителя", h))
    details = [
        ["Исполнитель", settings.executor_name],
        ["ИНН", settings.executor_inn or "не указан"],
        ["ОГРНИП", settings.executor_ogrnip or "не указан"],
        ["Email", settings.executor_email or "не указан"],
        ["Адрес", settings.executor_address or "не указан"],
    ]
    table = Table(details, colWidths=[35 * mm, 120 * mm])
    table.setStyle(TableStyle([
        ("FONTNAME", (0,0), (-1,-1), "ContractSans"),
        ("FONTSIZE", (0,0), (-1,-1), 8.5),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LINEBELOW", (0,0), (-1,-1), 0.25, colors.lightgrey),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("TOPPADDING", (0,0), (-1,-1), 4),
    ]))
    story.append(table)
    story.append(Spacer(1, 6 * mm))
    story.append(_p(
        f"Заказчик: {signer_name}\n"
        f"Telegram: @{client['telegram_username'] or 'не указан'}\n"
        f"Threads: @{client['threads_username_normalized']}\n"
        f"Версия договора: {contract_version(client, signer_name, settings)}",
        small,
    ))
    if draft:
        story.append(_p(
            "Этот файл является предварительным просмотром. Финальная версия формируется после того, "
            "как клиент подтвердит свои ФИО в Telegram-боте.",
            small,
        ))
    doc.build(story)
    return output_path


def generate_policy_pdf(settings, output_path: str) -> str:
    body, small, title, h = _styles()
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title="Политика обработки персональных данных",
        author=settings.executor_name,
    )
    story = [_p("ПОЛИТИКА ОБРАБОТКИ ПЕРСОНАЛЬНЫХ ДАННЫХ", title)]
    story.append(_p(
        f"Оператор: {settings.executor_name}, ИНН {settings.executor_inn}. "
        f"Контакт для обращений по персональным данным: {settings.executor_email}.",
        body,
    ))

    sections = [
        ("1. Общие положения",
         "Политика определяет порядок обработки персональных данных клиентов при использовании "
         "Telegram-бота и при оказании услуг по продвижению в Threads."),
        ("2. Какие данные обрабатываются",
         "ФИО; Telegram ID и username; Threads username; контактные данные, предоставленные клиентом; "
         "условия проекта и стоимость услуг; сообщения, файлы и материалы, направленные через бот; "
         "статистика проекта, включая предоставленные клиентом скриншоты и показатели."),
        ("3. Цели обработки",
         "Заключение и исполнение договора; идентификация клиента; предоставление контента и материалов; "
         "коммуникация с клиентом; ведение статистики и отчетности по проекту; подтверждение договорных "
         "действий; выполнение обязанностей, установленных законодательством."),
        ("4. Действия с данными",
         "Сбор, запись, систематизация, накопление, хранение, уточнение, использование, передача в случаях, "
         "необходимых для работы используемых сервисов, блокирование и удаление."),
        ("5. Используемые сервисы",
         "Взаимодействие с клиентом осуществляется в том числе через Telegram. Клиент также может получать "
         "ссылки на Google Sheets и иные согласованные рабочие сервисы. Такие сервисы обрабатывают данные "
         "в соответствии со своими правилами и политиками."),
        ("6. Срок обработки",
         "Данные обрабатываются в течение срока договорных отношений и далее в течение сроков, необходимых "
         "для исполнения обязанностей по законодательству и защиты законных интересов оператора. "
         "Данные, основанные исключительно на согласии, обрабатываются до достижения цели обработки "
         "или отзыва согласия, если иное основание обработки не предусмотрено законом."),
        ("7. Права субъекта",
         "Клиент вправе запросить сведения об обработке своих персональных данных, потребовать их уточнения, "
         "блокирования или удаления в предусмотренных законом случаях, а также отозвать согласие, направив "
         f"обращение на {settings.executor_email}."),
        ("8. Безопасность",
         "Оператор принимает разумные организационные и технические меры для защиты персональных данных "
         "от неправомерного доступа, изменения, раскрытия и уничтожения."),
        ("9. Согласие",
         "Согласие на обработку персональных данных запрашивается отдельно от договора. В Telegram-боте "
         "фиксируются Telegram ID, username, ФИО, дата и время предоставления согласия."),
    ]
    for head, text in sections:
        story.append(_p(head, h))
        story.append(_p(text, body))

    story.append(_p(f"Версия политики: {policy_version(settings)}", small))
    doc.build(story)
    return output_path


def temp_pdf(prefix: str) -> str:
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".pdf")
    os.close(fd)
    return path
