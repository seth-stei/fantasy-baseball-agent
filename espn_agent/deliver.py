#!/usr/bin/env python3
"""
Delivery module - prints the daily digest to terminal and sends via email.
Reuses SMTP configuration from the existing .env setup.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import date


def format_digest(
    team_name: str,
    lineup_text: str,
    lineup_analysis: str,
    trade_analysis: str = '',
    waiver_analysis: str = '',
    roster_moves: str = '',
    il_alerts: str = '',
    matchup_score: str = '',
) -> str:
    """Build the full daily digest text."""
    today = date.today().strftime('%A, %B %-d, %Y')
    lines = [
        f"{'='*60}",
        f"FANTASY BASEBALL DAILY BRIEFING",
        f"{today} | {team_name}",
        f"{'='*60}",
    ]

    if matchup_score:
        lines += ['', f'MATCHUP: {matchup_score}', '']

    if il_alerts:
        lines += [
            '',
            'IL ACTION NEEDED',
            '-'*40,
            il_alerts,
        ]

    lines += [
        '',
        "TODAY'S LINEUP",
        '-'*40,
        lineup_text,
        '',
        'AI ANALYSIS',
        '-'*40,
        lineup_analysis,
    ]

    if roster_moves:
        lines += [
            '',
            'ROSTER MOVES (EXECUTED TODAY)',
            '-'*40,
            roster_moves,
        ]

    if trade_analysis:
        lines += [
            '',
            'TRADE OPPORTUNITIES',
            '-'*40,
            trade_analysis,
        ]

    if waiver_analysis:
        lines += [
            '',
            'WAIVER WIRE (ADVISORY)',
            '-'*40,
            waiver_analysis,
        ]

    lines += ['', '='*60]
    return '\n'.join(lines)


def print_digest(digest: str):
    """Print the digest to terminal."""
    print(digest)


def send_email_digest(digest: str, subject: str = None):
    """Send the digest via email using existing SMTP config."""
    smtp_server = os.getenv('SMTP_SERVER')
    smtp_port = int(os.getenv('SMTP_PORT', 587))
    smtp_user = os.getenv('SMTP_USERNAME')
    smtp_pass = os.getenv('SMTP_PASSWORD')
    to_email = os.getenv('NOTIFICATION_EMAIL')

    if not all([smtp_server, smtp_user, smtp_pass, to_email]):
        print("\n⚠️  Email not configured - skipping email delivery")
        return False

    today = date.today().strftime('%B %-d')
    if not subject:
        subject = f"Fantasy Baseball Briefing - {today}"

    msg = MIMEMultipart('alternative')
    msg['From'] = smtp_user
    msg['To'] = to_email
    msg['Subject'] = subject

    msg.attach(MIMEText(digest, 'plain'))

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)
        server.quit()
        print(f"\n✓ Briefing emailed to {to_email}")
        return True
    except Exception as e:
        print(f"\n✗ Failed to send email: {e}")
        return False


def deliver(
    team_name: str,
    lineup_text: str,
    lineup_analysis: str,
    trade_analysis: str = '',
    waiver_analysis: str = '',
    roster_moves: str = '',
    il_alerts: str = '',
    matchup_score: str = '',
    send_email: bool = True,
):
    """Print and optionally email the daily digest."""
    digest = format_digest(
        team_name=team_name,
        lineup_text=lineup_text,
        lineup_analysis=lineup_analysis,
        trade_analysis=trade_analysis,
        waiver_analysis=waiver_analysis,
        roster_moves=roster_moves,
        il_alerts=il_alerts,
        matchup_score=matchup_score,
    )

    print_digest(digest)

    if send_email:
        send_email_digest(digest)

    return digest
