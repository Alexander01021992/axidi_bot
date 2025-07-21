# excel_utils.py
"""Модуль для создания Excel-файлов с данными о платежах и регистрациях."""

import pandas as pd
from datetime import datetime
import os
import logging
from typing import List, Tuple, Optional

logger = logging.getLogger(__name__)

def create_payments_excel(payments: List[Tuple], filename: str, start_date: str = None, end_date: str = None) -> Optional[str]:
    """Создает Excel-файл с данными о платежах."""
    try:
        columns = ['User ID', 'План', 'Сумма (RUB)', 'ID платежа', 'Дата платежа', 'Username', 'Имя']
        data = []
        for payment in payments:
            user_id, plan, amount, payment_id, payment_date, username, first_name = payment
            data.append({
                'User ID': user_id,
                'План': plan.capitalize() if plan else 'N/A',
                'Сумма (RUB)': f"{amount:.2f}" if amount else '0.00',
                'ID платежа': payment_id or 'N/A',
                'Дата платежа': payment_date.strftime('%Y-%m-%d %H:%M:%S') if payment_date else 'N/A',
                'Username': f"@{username}" if username and username != 'Без имени' else 'N/A',
                'Имя': first_name or 'N/A'
            })

        df = pd.DataFrame(data, columns=columns)

        title = 'Статистика платежей'
        if start_date and end_date:
            title += f' с {start_date} по {end_date}'
        elif start_date:
            title += f' за {start_date}'

        os.makedirs('temp', exist_ok=True)
        file_path = os.path.join('temp', filename)

        with pd.ExcelWriter(file_path, engine='xlsxwriter') as writer:
            df.to_excel(writer, sheet_name='Payments', index=False, startrow=2)
            worksheet = writer.sheets['Payments']
            worksheet.write(0, 0, title, writer.book.add_format({'bold': True, 'font_size': 14}))

            for idx, col in enumerate(df.columns):
                max_len = max(df[col].astype(str).map(len).max(), len(col)) + 2
                worksheet.set_column(idx, idx, max_len)

        logger.info(f"Excel-файл успешно создан: {file_path}")
        return file_path

    except Exception as e:
        logger.error(f"Ошибка создания Excel-файла платежей: {e}", exc_info=True)
        return None

def create_registrations_excel(registrations: List[Tuple], filename: str, date: str) -> Optional[str]:
    """Создает Excel-файл с данными о новых регистрациях."""
    try:
        columns = ['User ID', 'Username', 'Имя', 'Дата регистрации', 'Реферер ID']
        data = []
        for registration in registrations:
            user_id, username, first_name, created_at, referrer_id = registration
            data.append({
                'User ID': user_id,
                'Username': f"@{username}" if username and username != 'Без имени' else 'N/A',
                'Имя': first_name or 'N/A',
                'Дата регистрации': created_at.strftime('%Y-%m-%d %H:%M:%S') if created_at else 'N/A',
                'Реферер ID': referrer_id if referrer_id else 'N/A'
            })

        df = pd.DataFrame(data, columns=columns)

        title = f'Новые регистрации за {date}'

        os.makedirs('temp', exist_ok=True)
        file_path = os.path.join('temp', filename)

        with pd.ExcelWriter(file_path, engine='xlsxwriter') as writer:
            df.to_excel(writer, sheet_name='Registrations', index=False, startrow=2)
            worksheet = writer.sheets['Registrations']
            worksheet.write(0, 0, title, writer.book.add_format({'bold': True, 'font_size': 14}))

            for idx, col in enumerate(df.columns):
                max_len = max(df[col].astype(str).map(len).max(), len(col)) + 2
                worksheet.set_column(idx, idx, max_len)

        logger.info(f"Excel-файл регистраций успешно создан: {file_path}")
        return file_path

    except Exception as e:
        logger.error(f"Ошибка создания Excel-файла регистраций: {e}", exc_info=True)
        return None