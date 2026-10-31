"""Окно программы: страницы, мини-окно, окна-сообщения, тема и свои виджеты."""

from .ctk_fonts import import_customtkinter

import_customtkinter()  # раньше всех модулей окна: иначе в глубокой папке программа не запускалась (см. ctk_fonts.py)
