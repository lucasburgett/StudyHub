from datetime import date

from studyhub.util import clock, course_codes, find_date, lecture_number, parse_clock, title_key


def test_course_codes():
    assert course_codes("F26-CS-231N-01 CS 231N: Deep Learning (Autumn 2026)") == [("CS 231N", "CS231N")]
    assert course_codes("cs231n/Lecture notes.pdf") == [("CS 231N", "CS231N")]
    assert course_codes("MATH 51 / Lecture 3.pdf") == [("MATH 51", "MATH51")]
    assert course_codes("Week 2 notes") == []
    assert course_codes("Lecture 3") == []
    assert course_codes(None) == []


def test_lecture_number():
    assert lecture_number("cs231n_lecture_03_loss.pdf") == 3
    assert lecture_number("Lec 12 slides") == 12
    assert lecture_number("Lecture3.pdf") == 3
    assert lecture_number("L2 regularization notes") is None
    assert lecture_number("Assignment 1") is None


def test_find_date():
    assert find_date("9/24 — loss functions", 2026) == date(2026, 9, 24)
    assert find_date("Sept 29th: backprop", 2026) == date(2026, 9, 29)
    assert find_date("2026-10-01 midterm review", 2026) == date(2026, 10, 1)
    assert find_date("loss ~ log 10 ~ 2.3", 2026) is None  # a decimal, not February 3rd
    assert find_date("no dates here", 2026) is None


def test_clock_round_trip():
    assert clock(2472) == "41:12"
    assert clock(3909) == "1:05:09"
    assert parse_clock("41:12") == 2472
    assert parse_clock("1:05:09") == 3909
    assert parse_clock("abc") is None


def test_title_key_matches_across_sites():
    assert title_key("Homework 1") == title_key("HW 1")
    assert title_key("Problem Set 2") == title_key("PS2")
    assert title_key("Assignment 1") == title_key("assignment-1")
