import csv
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from operator import attrgetter
from pathlib import Path

import pdfplumber


logging.basicConfig(level=logging.INFO, format='%(asctime)-25s %(levelname)-9s %(message)s')
logger = logging.getLogger(__name__)

DATE_FMT = "%d.%m.%Y %H:%M:%S"
DATE_HEADER = "Дата и время"
FIELDNAMES = ['Расход', 'Дата', 'Описание']
CLEAN_PATTERN = re.compile(r'[\n\r\s]+')
AMOUNT_PATTERN = re.compile(r'[^\d.-]')
DATE_PATTERN = re.compile(r'\d{2}\.\d{2}\.\d{4}')


@dataclass
class Transaction:
    expense: float
    date: datetime
    description: str

    def to_csv_dict(self) -> dict:
        return {
            'Расход': self.expense,
            'Дата': self.date,
            'Описание': self.description
        }


def clean_value(text: str , is_amount: bool = False, ) -> str:
    if not text:
        return ''

    return CLEAN_PATTERN.sub(' ', text).strip()


def parse_amount(value: str | None) -> float:
    if not value:
        return 0.0

    try:
        return float(AMOUNT_PATTERN.sub('', str(value)))
    except ValueError:
        logger.warning(f"Не удалось преобразовать сумму: {value}")
        return 0.0


def parse_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None

    try:
        return datetime.strptime(clean_value(date_str), DATE_FMT)
    except ValueError as e:
        logger.error(f"Ошибка парсинга даты '{date_str}': {e}")
        return None


def create_transaction(
    expense: float,
    date: datetime,
    description: str,
    commission: float = 0.0
) -> Transaction:

    total = expense + commission
    desc = description.replace('Оплата товаров и услуг. ', '')

    if commission != 0:
        desc += f' Комиссия: {commission}'

    return Transaction(expense=total, date=date, description=desc)


def save_to_csv(operations: list[Transaction], output_path: str) -> None:
    if not operations:
        logger.warning(f"⚠ Нет операций для сохранения в {output_path}")
        return

    try:
        output_dir = Path(output_path).parent
        if not output_dir.exists():
            output_dir.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES, delimiter=';')
            writer.writeheader()
            writer.writerows(op.to_csv_dict() for op in operations)

        logger.info(f"✓ Данные сохранены в файл: {output_path}")
        logger.info(f"✓ Всего сохранено {len(operations)} операций")

    except OSError as e:
        logger.error(f"Ошибка при сохранении CSV файла {output_path}: {e}")


def parse_credit_ozon(row: list[str]) -> Transaction | None:
    date = parse_date(clean_value(row[0]))
    description = clean_value(row[2])
    expense = parse_amount(row[3])

    if expense > 0:
        return None
    elif "Перечисление денежных средств" in description:
        description = "Возврат денег, отмена заказа"
    else:
        description.replace(
            'Оплата товаров/услуг на Платформе', ''
        ).replace(
            '. Без НДС.', ''
        ).strip()
        expense = abs(expense)

    return create_transaction(expense, date, description)


def parse_credit_vtb(row: list[str]) -> Transaction | None:
    if len(row) < 7:
        return None

    expense = parse_amount(row[4])
    commission = parse_amount(row[5])
    date = parse_date(row[0])
    description = clean_value(row[-1])

    return create_transaction(expense, date, description, commission)


def parse_debit_vtb(row: list[str]) -> Transaction | None:
    expense = abs(parse_amount(row[2]))
    commission = parse_amount(row[4])
    date = parse_date(row[0])
    description = clean_value(row[5])

    if expense == 0 or 'Перевод между своими счетами' in description:
        return None

    return create_transaction(expense, date, description, commission)



def process_statement(pdf_path: str, csv_path: str, parse_func) -> None:
    pdf_file = Path(pdf_path)
    if not pdf_file.exists():
        logger.error(f"✗ Файл {pdf_path} не найден!")
        return

    if pdf_file.stat().st_size == 0:
        logger.error(f"✗ Файл {pdf_path} пустой!")
        return

    logger.info(f"📄 Обработка выписки: {pdf_path}")

    operations = []

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables():
                    for row in table:
                        if not row[0] or not DATE_PATTERN.search(row[0][:10]):
                            continue

                        try:
                            parse_row = parse_func(row)

                            if parse_row:
                                operations.append(parse_row)

                        except (IndexError, ValueError, TypeError) as e:
                            logger.error(f"Ошибка парсинга строки {row}: {e}")
                            continue

    except OSError as e:
        logger.error(f"Ошибка при открытии PDF файла {pdf_path}: {e}")

    if not operations:
        logger.warning(f"⚠ В файле {pdf_path} не найдено операций")

    operations.sort(key=attrgetter('date'))
    save_to_csv(operations, csv_path)
    logger.info(f"✓ Успешно извлечено {len(operations)} операций")


def main():
    files = [
        ("о1.pdf", "о1.csv", parse_credit_ozon),
        ("к1.pdf", "к1.csv", parse_credit_vtb),
        ("д1.pdf", "д1.csv", parse_debit_vtb),
    ]

    for pdf, csv_out, func in files:
        if Path(pdf).exists():
            process_statement(pdf, csv_out, func)
        else:
            logger.error(f"✗ Файл {pdf} не найден!")


if __name__ == "__main__":
    main()




