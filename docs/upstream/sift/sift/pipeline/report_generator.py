import json
import logging
import os
from datetime import datetime
from typing import Dict
from sift.models import ProductReport, ComparisonReport

logger = logging.getLogger(__name__)


def generate_markdown_report(report: ProductReport) -> str:
    lines = [
        f"# Product Feedback Analysis: {report.product}",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Total feedback collected: {report.total_feedback_count}",
        "",
        "## Overall Insights",
        report.overall_insights or "No overall insights generated.",
        "",
        "## Top Pain Points",
        "",
    ]
    for i, pp in enumerate(report.top_pain_points, 1):
        lines.append(f"{i}. **{pp}**")
    lines.append("")
    lines.append("## Clustered Complaint Themes")
    lines.append("")
    for cluster in report.clusters:
        severity_badge = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(cluster.severity or "medium", "⚪")
        lines.append(f"### {severity_badge} {cluster.label} ({cluster.size} complaints)")
        lines.append(f"**Severity:** {cluster.severity}")
        lines.append(f"**Summary:** {cluster.summary}")
        lines.append("")
        lines.append("**Representative quotes:**")
        for quote in cluster.representative_quotes:
            lines.append(f"- \"{quote}\"")
        lines.append("")

    return "\n".join(lines)


def generate_comparison_markdown(report: ComparisonReport) -> str:
    lines = [
        "# Competitive Product Comparison",
        f"Products: {', '.join(report.products)}",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Competitive Insights",
        report.competitive_insights or "No comparison insights generated.",
        "",
        "## Shared Pain Points",
        "",
    ]
    for i, pp in enumerate(report.shared_pain_points, 1):
        lines.append(f"{i}. {pp}")
    lines.append("")
    lines.append("## Unique Pain Points by Product")
    lines.append("")
    for product, points in report.unique_pain_points.items():
        lines.append(f"### {product}")
        for pp in points:
            lines.append(f"- {pp}")
        lines.append("")

    lines.append("## Individual Product Reports")
    lines.append("")
    for product, pr in report.product_reports.items():
        lines.append(f"### {product} — Top Issues")
        for cluster in pr.clusters[:5]:
            severity_badge = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(cluster.severity or "medium", "⚪")
            lines.append(f"- {severity_badge} **{cluster.label}** ({cluster.size} complaints): {cluster.summary}")
        lines.append("")

    return "\n".join(lines)


def save_reports(
    product_reports: Dict[str, ProductReport],
    comparison_report: ComparisonReport,
    output_dir: str = "output",
) -> list[str]:
    """Save all reports to disk. Returns the list of saved file paths."""
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    saved: list[str] = []

    for product, report in product_reports.items():
        slug = product.lower().replace(" ", "_")

        md_path = os.path.join(output_dir, f"{slug}_{timestamp}.md")
        with open(md_path, "w") as f:
            f.write(generate_markdown_report(report))
        logger.info("Saved markdown report: %s", md_path)
        saved.append(md_path)

        json_path = os.path.join(output_dir, f"{slug}_{timestamp}.json")
        with open(json_path, "w") as f:
            json.dump(report.to_dict(), f, indent=2)
        logger.info("Saved JSON report: %s", json_path)
        saved.append(json_path)

    md_path = os.path.join(output_dir, f"comparison_{timestamp}.md")
    with open(md_path, "w") as f:
        f.write(generate_comparison_markdown(comparison_report))
    logger.info("Saved comparison markdown: %s", md_path)
    saved.append(md_path)

    json_path = os.path.join(output_dir, f"comparison_{timestamp}.json")
    with open(json_path, "w") as f:
        json.dump(comparison_report.to_dict(), f, indent=2)
    logger.info("Saved comparison JSON: %s", json_path)
    saved.append(json_path)

    return saved
