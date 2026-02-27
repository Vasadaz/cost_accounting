import pdfplumber
import csv
import re
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from operator import attrgetter

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)-25s %(levelname)-9s %(message)s')
logger = logging.getLogger(__name__)

# Константы
DATE_FMT = "%d.%m.%Y %H:%M:%S"
DATE_HEADER = "Дата и время"
FIELDNAMES = ['Расход', 'Дата', 'Описание']
CLEAN_PATTERN = re.compile(r'[\n\r\s]+')
AMOUNT_PATTERN = re.compile(r'[,\sRUB]+')
DATE_PATTERN = re.compile(r'\d{2}\.\d{2}\.\d{4}')


@dataclass
class Transaction:
    """Банковская операция."""
    expense: float
    date: datetime
    description: str

    def to_csv_dict(self) -> dict:
        return {
            'Расход': self.expense,
            'Дата': self.date,
            'Описание': self.description
        }


def clean_value(text: str | None, is_amount: bool = False) -> str:
    """Универсальная функция очистки значений."""
    if not text:
        return ''

    # Убираем переносы и множественные пробелы за один проход
    text = CLEAN_PATTERN.sub(' ', text).strip()

    if is_amount:
        text = AMOUNT_PATTERN.sub('', text)

    return text


def parse_amount(value: str | None) -> float:
    """Парсит строку в число."""
    cleaned = clean_value(value, is_amount=True)
    return float(cleaned) if cleaned else 0.0


def parse_date(value: str | None) -> datetime:
    """Парсит строку в дату."""
    return datetime.strptime(clean_value(value), DATE_FMT)


def create_transaction(expense: float, commission: float, date: datetime, description: str) -> Transaction:
    """Создаёт объект транзакции."""
    total = expense + commission
    desc = description.replace('Оплата товаров и услуг. ', '')

    if commission != 0:
        desc += f' Комиссия: {commission}'

    return Transaction(expense=total, date=date, description=desc)


def save_to_csv(operations: list[Transaction], output_path: str) -> None:
    """Сохраняет данные в CSV файл."""
    if not operations:
        logger.warning(f"⚠ Нет операций для сохранения в {output_path}")
        return

    try:
        with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES, delimiter=';')
            writer.writeheader()
            writer.writerows(op.to_csv_dict() for op in operations)

        logger.info(f"✓ Данные сохранены в файл: {output_path}")

    except OSError as e:
        logger.error(f"Ошибка при сохранении CSV файла {output_path}: {e}")


def parse_debit_pdf(pdf_path: str) -> list[Transaction]:
    """Парсит дебетовую выписку."""
    operations = []

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables():
                    if not table:
                        continue

                    # Ищем таблицу с операциями по заголовкам
                    header_row = next(
                        (idx for idx, row in enumerate(table)
                         if row and any(DATE_HEADER in str(cell) for cell in row if cell)),
                        None
                    )

                    if header_row is None:
                        continue

                    for row in table[header_row + 1:]:
                        if not row or not any(row):
                            continue

                        if not row[0] or not DATE_PATTERN.search(str(row[0])):
                            continue

                        try:
                            expense = parse_amount(row[4])
                            commission = parse_amount(row[5])
                            date = parse_date(row[0])
                            description = clean_value(row[6])

                            # Пропускаем пополнения и переводы между счетами
                            if expense == 0 or 'Перевод между своими счетами' in description:
                                logger.warning(f"⚠ Удаление операции: {row}")
                                continue

                            operations.append(
                                create_transaction(expense, commission, date, description)
                            )

                        except (IndexError, TypeError, ValueError) as e:
                            logger.error(f"Ошибка парсинга строки дебетовой операции: {e}")

    except OSError as e:
        logger.error(f"Ошибка при открытии PDF файла {pdf_path}: {e}")
        return []

    operations.sort(key=attrgetter('date'))
    return operations


def parse_credit_pdf(pdf_path: str) -> list[Transaction]:
    """Парсит кредитную выписку."""
    operations = []

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables():
                    if not table or len(table) < 2:
                        continue

                    header = " ".join(filter(None, table[0]))

                    if "Дата изменения" in header or "Проведена" not in header:
                        continue

                    is_completed = "Задолжен" in header
                    desc_idx = 7 if is_completed else 6

                    for row in table[1:]:
                        if not row or not row[0]:
                            continue

                        try:
                            expense = parse_amount(row[4])
                            commission = parse_amount(row[5])
                            date = parse_date(row[0])
                            description = clean_value(row[desc_idx])

                            operations.append(
                                create_transaction(expense, commission, date, description)
                            )

                        except (IndexError, TypeError, ValueError) as e:
                            logger.error(f"Ошибка парсинга строки кредитной операции: {e}")

    except OSError as e:
        logger.error(f"Ошибка при открытии PDF файла {pdf_path}: {e}")
        return []

    operations.sort(key=attrgetter('date'))
    return operations


def process_statement(pdf_path: str, csv_path: str, as_debit: bool = False) -> None:
    """Обрабатывает выписку и сохраняет в CSV."""
    logger.info(f"📄 Обработка выписки: {pdf_path}")

    parser = parse_debit_pdf if as_debit else parse_credit_pdf
    operations = parser(pdf_path)

    if not operations:
        logger.warning(f"⚠ В файле {pdf_path} не найдено операций")
        return

    save_to_csv(operations, csv_path)
    logger.info(f"✓ Успешно извлечено {len(operations)} операций")


def main():
    files = [
        ("к1.pdf", "к1.csv", False),
        ("д1.pdf", "д1.csv", True),
    ]

    for pdf, csv_out, is_debit in files:
        if Path(pdf).exists():
            process_statement(pdf, csv_out, as_debit=is_debit)
        else:
            logger.error(f"✗ Файл {pdf} не найден!")


if __name__ == "__main__":
    main()
