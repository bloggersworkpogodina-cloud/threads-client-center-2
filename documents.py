from __future__ import annotations

import hashlib
import html
import os
import re
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


TEMPLATE_VERSION = "2026-08-06-v3"
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
    fields = [
        TEMPLATE_VERSION, str(client["id"]), signer_name.strip(),
        client["legal_name"] or "", client["customer_type"] or "",
        client["customer_inn"] or "", client["customer_tax_status"] or "",
        client["customer_ogrn"] or "", client["customer_kpp"] or "",
        client["customer_address"] or "", client["customer_email"] or "",
        client["customer_phone"] or "", client["signer_authority"] or "",
        client["services"] or "", str(client["service_price"] or 0),
        client["billing_start"] or "", settings.executor_name,
        settings.executor_inn,
    ]
    return hashlib.sha256("|".join(fields).encode("utf-8")).hexdigest()[:20]


def policy_version(settings) -> str:
    payload = "|".join([
        POLICY_VERSION, settings.executor_name, settings.executor_inn,
        settings.executor_email,
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _p(text: str, style) -> Paragraph:
    return Paragraph(html.escape(text).replace("\n", "<br/>"), style)


def _clean_platform_words(text: str) -> str:
    text = re.sub(r"(?i)\bthreads\b", "цифрового контента", text or "")
    text = re.sub(r"(?i)\bmeta\b", "", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _customer_status_label(kind: str | None) -> str:
    return {
        "individual": "физическое лицо",
        "self_employed": "плательщик налога на профессиональный доход",
        "ip": "индивидуальный предприниматель",
        "company": "юридическое лицо",
    }.get(kind or "", "Заказчик")


def generate_contract_pdf(client, signer_name: str, settings, output_path: str, draft: bool = False) -> str:
    body, small, title, h = _styles()
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title="Договор возмездного оказания услуг",
        author=settings.executor_name,
    )

    ps, pe, first_due = period(client["billing_start"], 0)
    contract_no = f"УСЛ-{client['id']}-{datetime.now().strftime('%Y%m%d')}"
    services = _clean_platform_words(client["services"] or "Создание, публикация и аналитика цифрового контента")
    customer_status = _customer_status_label(client["customer_type"])

    if client["customer_type"] == "company":
        customer_intro = (
            f"{client['legal_name']}, ИНН {client['customer_inn']}, КПП {client['customer_kpp'] or '—'}, "
            f"ОГРН {client['customer_ogrn'] or '—'}, в лице {signer_name}, "
            f"действующего на основании {client['signer_authority'] or 'указанных полномочий'}, "
            "именуемое в дальнейшем «Заказчик»"
        )
    elif client["customer_type"] == "ip":
        customer_intro = (
            f"Индивидуальный предприниматель {client['legal_name']}, "
            f"ИНН {client['customer_inn']}, ОГРНИП {client['customer_ogrn'] or '—'}, "
            "именуемый(ая) в дальнейшем «Заказчик»"
        )
    else:
        customer_intro = (
            f"{client['legal_name']}, ИНН {client['customer_inn']}, "
            f"статус: {customer_status}, именуемый(ая) в дальнейшем «Заказчик»"
        )

    story = [
        _p(f"ДОГОВОР № {contract_no}<br/>ВОЗМЕЗДНОГО ОКАЗАНИЯ УСЛУГ", title),
        _p(f"Дата формирования: {datetime.now().strftime('%d.%m.%Y')}", small),
        _p(
            f"{settings.executor_name}, ИНН {settings.executor_inn}, ОГРНИП "
            f"{settings.executor_ogrnip or '—'}, именуемая в дальнейшем «Исполнитель», с одной стороны, "
            f"и {customer_intro}, с другой стороны, совместно именуемые «Стороны», "
            "заключили настоящий Договор о нижеследующем.",
            body,
        ),
    ]

    sections = [
        ("1. ПРЕДМЕТ ДОГОВОРА", [
            "1.1. Исполнитель обязуется по заданию Заказчика оказывать услуги по созданию, подготовке, публикации и анализу цифрового контента, а также иные услуги, указанные в Приложении № 1, а Заказчик обязуется принять и оплатить услуги.",
            "1.2. Конкретный перечень услуг, стоимость, дата начала оказания услуг, дата первого платежа, расчётный период и иные индивидуальные условия определяются Приложением № 1, являющимся неотъемлемой частью Договора.",
            "1.3. Услуги оказываются дистанционно с использованием сети Интернет, Telegram, Telegram-бота Исполнителя и иных согласованных Сторонами средств связи.",
            "1.4. Исполнитель самостоятельно определяет способы, методы, инструменты и технологию оказания услуг, если иное письменно не согласовано Сторонами.",
            "1.5. Настоящий Договор не является трудовым, агентским договором, договором поручения или договором о совместной деятельности.",
        ]),
        ("2. СТОИМОСТЬ И ПОРЯДОК ОПЛАТЫ", [
            "2.1. Стоимость услуг определяется Приложением № 1.",
            f"2.2. Первый платёж производится не позднее {fmt(first_due)}.",
            "2.3. Начиная со второго расчётного периода оплата производится ежемесячно, не позднее чем за 7 (семь) календарных дней до начала очередного расчётного периода.",
            "2.4. Датой исполнения обязанности по оплате считается дата поступления денежных средств Исполнителю.",
            "2.5. При просрочке оплаты Исполнитель вправе приостановить оказание услуг, перенести сроки выполнения работ и не приступать к новому расчётному периоду до поступления полной оплаты.",
        ]),
        ("3. ПОРЯДОК ОКАЗАНИЯ УСЛУГ", [
            "3.1. Услуги оказываются дистанционно в течение оплаченного расчётного периода.",
            "3.2. Если перечнем услуг предусмотрено создание регулярного контента, Исполнитель осуществляет подготовку контента согласно графику, указанному в Приложении № 1.",
            "3.3. Заказчик своевременно предоставляет информацию, материалы, фотографии, тексты, согласования, доступы и иные сведения, необходимые для оказания услуг.",
            "3.4. Непредоставление Заказчиком необходимых сведений является основанием для переноса сроков на период задержки и не считается нарушением обязательств Исполнителя.",
            "3.5. Стоимость расчётного периода не уменьшается, когда невозможность выполнения отдельных работ вызвана действиями или бездействием Заказчика.",
            "3.6. Исполнитель вправе использовать программное обеспечение, сервисы автоматизации и технологии искусственного интеллекта.",
        ]),
        ("4. ПРАВА И ОБЯЗАННОСТИ СТОРОН", [
            "4.1. Исполнитель обязуется оказывать услуги добросовестно, соблюдать согласованный перечень услуг, уведомлять о препятствующих обстоятельствах и обеспечивать конфиденциальность полученной информации.",
            "4.2. Исполнитель вправе запрашивать необходимые материалы и доступы, приостанавливать услуги при просрочке оплаты, переносить сроки при задержке информации и отказывать в выполнении задач, не предусмотренных Приложением № 1.",
            "4.3. Заказчик обязуется своевременно оплачивать услуги, предоставлять достоверные материалы и доступы, рассматривать направленные материалы и сообщать об изменении реквизитов.",
            "4.4. Исполнитель не обязан переносить на последующие периоды объём работ, не выполненный по причине действий или бездействия Заказчика, без отдельного соглашения.",
        ]),
        ("5. ПРИЁМКА УСЛУГ", [
            "5.1. По окончании каждого расчётного периода Исполнитель формирует Акт оказанных услуг в PDF и направляет его Заказчику через Telegram-бот.",
            "5.2. Акт содержит период, фактически оказанные услуги, их стоимость и результаты оказания услуг при наличии показателей.",
            "5.3. При отсутствии замечаний Заказчик подписывает Акт посредством функционала Telegram-бота.",
            "5.4. При наличии замечаний Заказчик направляет их до подписания Акта через Telegram-бот либо иным согласованным письменным способом.",
            "5.5. Подписание Акта подтверждает оказание услуг в согласованном объёме и их принятие без замечаний.",
        ]),
        ("6. ОТВЕТСТВЕННОСТЬ СТОРОН", [
            "6.1. Стороны несут ответственность в соответствии с законодательством Российской Федерации и условиями Договора.",
            "6.2. Исполнитель не гарантирует конкретное количество просмотров, подписчиков, заявок, обращений, продаж, охватов или иной маркетинговый результат.",
            "6.3. Исполнитель не отвечает за изменения алгоритмов, технические сбои сторонних сервисов, блокировки, ограничения функционала, действия третьих лиц и иные обстоятельства вне контроля Исполнителя.",
            "6.4. Совокупная ответственность Исполнителя ограничивается стоимостью услуг за расчётный период, в котором возникло основание требования, если иное не установлено обязательными нормами закона.",
        ]),
        ("7. КОНФИДЕНЦИАЛЬНОСТЬ", [
            "7.1. Информация, документы, переписка, коммерческие условия, аналитика и рабочие материалы, полученные при исполнении Договора, являются конфиденциальными.",
            "7.2. Заказчик не вправе передавать третьим лицам внутренние инструкции, шаблоны, базы данных, рабочие таблицы, промпты, сценарии автоматизации и иные внутренние материалы Исполнителя.",
            "7.3. Обязанность соблюдать конфиденциальность действует в течение 3 (трёх) лет после прекращения Договора.",
        ]),
        ("8. ИНТЕЛЛЕКТУАЛЬНАЯ СОБСТВЕННОСТЬ", [
            "8.1. Исключительные права на методики, шаблоны, документы, программный код, Telegram-ботов, автоматизации, промпты, инструкции и внутренние процессы принадлежат Исполнителю.",
            "8.2. Заказчику предоставляется право использовать результаты, созданные непосредственно для него в рамках Договора.",
            "8.3. Внутренние материалы Исполнителя не могут копироваться, распространяться, продаваться или передаваться третьим лицам без письменного согласия Исполнителя.",
        ]),
        ("9. СРОК ДЕЙСТВИЯ И ПРЕКРАЩЕНИЕ", [
            "9.1. Договор вступает в силу с момента подписания и действует до прекращения Сторонами либо полного исполнения обязательств.",
            "9.2. Сторона вправе отказаться от Договора, уведомив другую Сторону не менее чем за 14 календарных дней.",
            "9.3. При прекращении Договора сохраняется обязанность оплатить услуги, фактически оказанные до даты прекращения.",
        ]),
        ("10. ВОЗВРАТ ДЕНЕЖНЫХ СРЕДСТВ", [
            "10.1. При прекращении Договора после начала расчётного периода Исполнитель производит перерасчёт исходя из объёма услуг, фактически оказанных на дату прекращения.",
            "10.2. Сумма возврата определяется как разница между полученной оплатой и стоимостью фактически оказанных услуг, определяемой пропорционально объёму выполненных работ.",
            "10.3. При определении объёма учитываются подготовленные материалы, опубликованный контент, аналитические отчёты и иные результаты, предусмотренные Приложением № 1.",
            "10.4. Стоимость услуг, принятых Заказчиком путём подписания Акта, возврату не подлежит.",
            "10.5. Если часть услуг не выполнена по причинам, зависящим от Заказчика, это не признаётся нарушением Исполнителя и учитывается при расчёте возврата.",
            "10.6. При споре объём оказанных услуг определяется по Актам, аналитическим отчётам, сведениям о публикациях, внутреннему учёту выполненных работ и иным подтверждающим документам.",
            "10.7. Возврат производится в течение 10 рабочих дней после определения суммы возврата.",
        ]),
        ("11. ЭЛЕКТРОННОЕ ВЗАИМОДЕЙСТВИЕ", [
            "11.1. Стороны осуществляют обмен информацией, документами, уведомлениями и сообщениями посредством Telegram, Telegram-бота, электронной почты и иных согласованных каналов.",
            "11.2. Документы, направленные через Telegram-бот или согласованный канал, признаются направленными надлежащим образом с момента отправки.",
            "11.3. Заказчик обеспечивает доступ к указанным средствам связи и своевременно знакомится с поступающими документами.",
            "11.4. Заказчик обязан уведомлять Исполнителя об изменении контактных данных.",
        ]),
        ("12. ЗАКЛЮЧИТЕЛЬНЫЕ ПОЛОЖЕНИЯ", [
            "12.1. Споры урегулируются путём переговоров и претензионного порядка, а при недостижении соглашения — в соответствии с законодательством Российской Федерации.",
            "12.2. Во всём, что не урегулировано Договором, Стороны руководствуются законодательством Российской Федерации.",
            "12.3. Приложение № 1 является неотъемлемой частью Договора.",
        ]),
    ]

    for heading, paragraphs in sections:
        story.append(_p(heading, h))
        for paragraph in paragraphs:
            story.append(_p(paragraph, body))

    story.extend([
        _p("ПРИЛОЖЕНИЕ № 1. ИНДИВИДУАЛЬНЫЕ УСЛОВИЯ", title),
        _p(f"Перечень услуг: {services}", body),
        _p(f"Стоимость одного расчётного периода: {money(client['service_price'])}", body),
        _p(f"Первый расчётный период: {fmt(ps)} — {fmt(pe)}", body),
        _p(f"Дата первого платежа: не позднее {fmt(first_due)}", body),
        _p("Последующие платежи: ежемесячно, не позднее чем за 7 календарных дней до начала очередного расчётного периода.", body),
        _p("График оказания услуг: в соответствии с согласованным перечнем услуг. При ежедневной подготовке контента — каждый день, кроме субботы.", body),
        _p("РЕКВИЗИТЫ СТОРОН", title),
    ])

    rows = [
        ["ИСПОЛНИТЕЛЬ", "ЗАКАЗЧИК"],
        [settings.executor_name, client["legal_name"]],
        [f"ИНН: {settings.executor_inn}", f"Статус: {customer_status}"],
        [f"ОГРНИП: {settings.executor_ogrnip or '—'}", f"ИНН: {client['customer_inn']}"],
        [f"Email: {settings.executor_email}", f"ОГРН/ОГРНИП: {client['customer_ogrn'] or '—'}"],
        [f"Адрес: {settings.executor_address or '—'}", f"КПП: {client['customer_kpp'] or '—'}"],
        ["", f"Налогообложение: {client['customer_tax_status']}"],
        ["", f"Адрес: {client['customer_address']}"],
        ["", f"Email: {client['customer_email']}"],
        ["", f"Телефон: {client['customer_phone']}"],
        ["", f"Подписант: {signer_name}"],
    ]
    table = Table(rows, colWidths=[77 * mm, 77 * mm])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "ContractSans"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(table)

    story.append(_p(
        ("ПРЕДВАРИТЕЛЬНЫЙ ПРОСМОТР. " if draft else "") +
        f"Заказчик подтверждает договор через Telegram-бот. "
        f"Подписант: {signer_name}. Версия: {contract_version(client, signer_name, settings)}.",
        small,
    ))

    doc.build(story)
    return output_path
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


def generate_act_pdf(
    client,
    act,
    settings,
    output_path: str,
    *,
    signed: bool = False,
    signed_at: str | None = None,
    signer_name: str | None = None,
    signer_telegram_id: int | None = None,
) -> str:
    import json
    body, small, title, h = _styles()
    pdf = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"Акт оказанных услуг № {act['act_number']}",
        author=settings.executor_name,
    )

    results = json.loads(act["results_json"] or "{}")
    story = [
        _p(f"АКТ ОКАЗАННЫХ УСЛУГ № {act['act_number']}", title),
        _p(
            f"к договору возмездного оказания услуг\n"
            f"Период: {act['period_start']} — {act['period_end']}",
            small,
        ),
        _p(
            f"{settings.executor_name}, ИНН {settings.executor_inn}, именуемая «Исполнитель», "
            f"и {client['legal_name'] or client['name']}, именуемый(ая) «Заказчик», "
            "составили настоящий Акт о нижеследующем.",
            body,
        ),
        _p("1. Оказанные услуги", h),
        _p(act["services_text"], body),
        _p("2. Результаты за расчётный период", h),
        _p(
            f"Опубликовано веток (постов): {results.get('published_posts', 0)}\n"
            f"Подготовлено и предоставлено аналитических отчётов: {results.get('analytics_count', 1)}\n\n"
            f"Общие просмотры аккаунта: {results.get('views_start', 0):,} → {results.get('views_end', 0):,} "
            f"({results.get('views_growth', 0):+,})\n"
            f"Подписчики Threads: {results.get('threads_start', 0):,} → {results.get('threads_end', 0):,} "
            f"({results.get('threads_growth', 0):+,})\n"
            f"Подписчики Telegram: {results.get('telegram_start', 0):,} → {results.get('telegram_end', 0):,} "
            f"({results.get('telegram_growth', 0):+,})\n"
            f"Заявки за период: {results.get('applications', 0):,}",
            body,
        ),
        _p("3. Стоимость услуг", h),
        _p(f"Стоимость оказанных услуг за период составляет {money(act['amount'])}.", body),
        _p("4. Приёмка услуг", h),
        _p(
            "Заказчик подтверждает, что указанные в настоящем Акте услуги оказаны. "
            "При наличии замечаний Заказчик направляет их через Telegram-бот до подписания Акта.",
            body,
        ),
    ]

    if signed:
        story.append(_p("5. Электронное подписание", h))
        story.append(_p(
            f"Акт подписан Заказчиком в Telegram-боте.\n"
            f"Подписант: {signer_name or client['legal_name'] or client['name']}\n"
            f"Telegram ID: {signer_telegram_id or '—'}\n"
            f"Дата и время подписания: {signed_at or datetime.now().isoformat(timespec='seconds')}",
            body,
        ))
    else:
        story.append(_p(
            "Документ направлен Заказчику для ознакомления и электронного подписания в Telegram-боте.",
            small,
        ))

    details = [
        ["Исполнитель", settings.executor_name],
        ["ИНН Исполнителя", settings.executor_inn or "не указан"],
        ["Заказчик", client["legal_name"] or client["name"]],
        ["Стоимость", money(act["amount"])],
    ]
    table = Table(details, colWidths=[42 * mm, 113 * mm])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "ContractSans"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(table)

    pdf.build(story)
    return output_path
    return output_path
