# SPDX-License-Identifier: MIT
"""
yf_image_tools.py — YF Image ecosystem @tool file.

Registers 7 tools for the opprime-core-v2 tool system:
 1. yf_generate_image     — T2I via YF-image-base
 2. yf_recognize_image    — VLM image understanding
 3. yf_optimize_text      — LLM text optimization
 4. yf_create_infographic — Infographic generation (WIP delegates)
 5. yf_imitate_style      — Style imitation (WIP delegates)
 6. yf_create_resume_image — Resume image (WIP delegates)
 7. yf_create_ppt         — PPT generation (WIP delegates)
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from lib.toolkit import tool

# ── Path Resolution ──────────────────────────────────────────────────────────

SKILL_DIR = os.environ.get("YF_IMAGE_BASE_DIR") or os.path.expanduser(
    "~/.qclaw/skills/YF-image-base"
)
RUNNER = str(Path(SKILL_DIR) / "scripts" / "yf_agent_runner.py")

INF_DIR = os.path.expanduser("~/.qclaw/skills/YF-infographic")
IMIT_DIR = os.path.expanduser("~/.qclaw/skills/YF-image-imitate")
RESUME_DIR = os.path.expanduser("~/.qclaw/skills/YF-image-resume")
PPT_DIR = os.path.expanduser("~/.qclaw/skills/YF-ppt-pro")


def _run_runner(tool_name: str, *args, timeout: int = 120) -> dict:
    """Call yf_agent_runner.py and return parsed JSON result."""
    cmd = [sys.executable, RUNNER, tool_name] + list(args) + ["--output-format", "json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            return {"status": "error", "error": r.stderr.strip() or r.stdout.strip()}
        return json.loads(r.stdout)
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": f"Command timed out after {timeout}s"}
    except json.JSONDecodeError as e:
        return {"status": "error", "error": f"JSON parse error: {e}"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── Tool Implementations ─────────────────────────────────────────────────────


@tool("yf_generate_image")
def yf_generate_image(prompt: str, aspect_ratio: str = "16:9",
                       image_size: str = "2k",
                       save_path: str = "") -> str:
    """
    Generate an image from text description using SiliconFlow T2I.

    Args:
        prompt: Text description of the image to generate (required)
        aspect_ratio: Output aspect ratio (16:9, 9:16, 1:1, 4:3, 3:4)
        image_size: Size preset (1k, 2k)
        save_path: Optional custom save path

    Returns:
        JSON string with status and image_path
    """
    args = ["--prompt", prompt, "--aspect-ratio", aspect_ratio, "--image-size", image_size]
    if save_path:
        args += ["--save-path", save_path]

    result = _run_runner("yf-image-generate", *args)
    return json.dumps(result, ensure_ascii=False)


@tool("yf_recognize_image")
def yf_recognize_image(image_path: str, system_prompt: str = "",
                        user_prompt: str = "Describe this image in detail.") -> str:
    """
    Analyze an image using VLM (Qwen3-VL).

    Args:
        image_path: Path to the image file (required)
        system_prompt: Optional system prompt for the VLM
        user_prompt: Question or instruction about the image

    Returns:
        JSON string with status and text analysis result
    """
    args = ["--images", image_path, "--user-prompt", user_prompt]
    if system_prompt:
        args = ["--system-prompt", system_prompt] + args

    result = _run_runner("yf-image-recognize", *args)
    return json.dumps(result, ensure_ascii=False)


@tool("yf_optimize_text")
def yf_optimize_text(user_prompt: str, system_prompt: str = "") -> str:
    """
    Process/optimize text using LLM (DeepSeek).

    Args:
        user_prompt: Text to process or question to ask (required)
        system_prompt: Optional system prompt for the LLM

    Returns:
        JSON string with status and processed text result
    """
    args = ["--user-prompt", user_prompt]
    if system_prompt:
        args = ["--system-prompt", system_prompt] + args

    result = _run_runner("yf-text-optimize", *args)
    return json.dumps(result, ensure_ascii=False)


@tool("yf_create_infographic")
def yf_create_infographic(content: str, layout: str = "auto",
                           style: str = "professional",
                           orientation: str = "landscape") -> str:
    """
    Create an infographic from content using the YF-infographic pipeline.

    Args:
        content: Text/data to visualize (required)
        layout: Layout template (auto, comparison, timeline, big_numbers, etc.)
        style: Visual style (professional, playful, minimalist, bold, nature, tech)
        orientation: landscape or portrait

    Returns:
        JSON string with status and image_path
    """
    # Stage 1: Analyze content and select layout
    if layout == "auto":
        analysis_prompt = (
            f"Analyze this content and select the best infographic layout template. "
            f"Content: {content[:500]}...\n"
            f"Options: comparison_side_by_side, timeline_horizontal, timeline_vertical, "
            f"big_numbers, process_flow, card_grid, single_message, centered_focus, "
            f"data_dashboard, split_horizontal\n"
            f"Return ONLY the template name."
        )
        analysis = _run_runner("yf-text-optimize",
                               "--user-prompt", analysis_prompt,
                               "--system-prompt",
                               "You are an infographic layout expert. Return only the template name.")
        if analysis.get("status") == "ok":
            layout = analysis["result"].strip().lower()
        else:
            layout = "comparison_side_by_side"

    # Stage 2: Build the T2I prompt
    prompt_text = (
        f"Design a professional {style} infographic in {orientation} orientation "
        f"using the {layout} layout template. Content to visualize:\n\n{content}\n\n"
        "Follow infographic design rules: clear hierarchy, consistent colors, "
        "adequate white space, readable fonts. Generate a publication-ready infographic."
    )

    # Stage 3: Generate
    result = _run_runner("yf-image-generate",
                         "--prompt", prompt_text,
                         "--aspect-ratio", "16:9" if orientation == "landscape" else "9:16",
                         "--image-size", "2k")
    return json.dumps(result, ensure_ascii=False)


@tool("yf_imitate_style")
def yf_imitate_style(reference_image: str, target_content: str) -> str:
    """
    Generate a new image that imitates the style of a reference image
    while replacing the content.

    Args:
        reference_image: Path to the reference image (required)
        target_content: Description of the new content (required)

    Returns:
        JSON string with status and generated image path
    """
    # Stage 1: Annotate reference image
    annotate_result = _run_runner(
        "yf-image-recognize",
        "--images", reference_image,
        "--system-prompt-path", str(Path(IMIT_DIR) / "prompts" / "image_annotate.md"),
        "--user-prompt", "Please annotate this reference image with long caption and layout blueprint."
    )
    if annotate_result.get("status") != "ok":
        return json.dumps({"status": "error",
                           "error": f"Annotation failed: {annotate_result.get('error', 'unknown')}"},
                          ensure_ascii=False)

    # Stage 2: Rewrite caption
    annotation = annotate_result["result"]
    rewrite_prompt = (
        f"Reference annotation:\n{annotation}\n\n"
        f"Target content:\n{target_content}\n\n"
        "Rewrite the long caption to match target content while preserving layout and style."
    )
    rewrite_result = _run_runner(
        "yf-text-optimize",
        "--system-prompt-path", str(Path(IMIT_DIR) / "prompts" / "caption_rewrite.md"),
        "--user-prompt", rewrite_prompt,
    )
    if rewrite_result.get("status") != "ok":
        return json.dumps({"status": "error",
                           "error": f"Caption rewrite failed: {rewrite_result.get('error', 'unknown')}"},
                          ensure_ascii=False)

    # Stage 3: Generate image with rewritten caption
    new_caption = rewrite_result["result"]
    output_path = f"/tmp/yf-imitate-{int(__import__('time').time())}.png"
    gen_result = _run_runner(
        "yf-image-generate",
        "--prompt", new_caption,
        "--save-path", output_path,
    )
    if gen_result.get("status") == "ok":
        gen_result["imitated_from"] = reference_image
        gen_result["original_annotation"] = annotation[:200] + "..."

    return json.dumps(gen_result, ensure_ascii=False)


@tool("yf_create_resume_image")
def yf_create_resume_image(resume_content: str, style: str = "",
                            aspect_ratio: str = "9:16") -> str:
    """
    Generate a designed portfolio-resume image from resume content.

    Args:
        resume_content: Resume text with name, experience, skills, etc. (required)
        style: Optional visual style preference
        aspect_ratio: Output aspect ratio (default 9:16)

    Returns:
        JSON string with status and image_path
    """
    # Use resume template prompt to build T2I prompt
    sys_prompt_path = str(Path(RESUME_DIR) / "prompts" / "resume_template.md")
    user_prompt = f"Resume content:\n{resume_content}"
    if style:
        user_prompt += f"\nStyle preference: {style}"

    prompt_result = _run_runner(
        "yf-text-optimize",
        "--system-prompt-path", sys_prompt_path,
        "--user-prompt", user_prompt,
    )
    if prompt_result.get("status") != "ok":
        return json.dumps({"status": "error",
                           "error": f"Prompt generation failed: {prompt_result.get('error', 'unknown')}"},
                          ensure_ascii=False)

    generation_prompt = prompt_result["result"]
    result = _run_runner(
        "yf-image-generate",
        "--prompt", generation_prompt,
        "--aspect-ratio", aspect_ratio,
        "--image-size", "2k",
    )
    return json.dumps(result, ensure_ascii=False)


@tool("yf_create_ppt")
def yf_create_ppt(topic: str, slides: int = 8, mode: str = "standard",
                   audience: str = "general", tone: str = "professional") -> str:
    """
    Generate a PPT presentation on a given topic.

    Args:
        topic: Presentation topic or content (required)
        slides: Target number of slides (default 8)
        mode: standard (PPTX export) or creative (image-based) (default standard)
        audience: Target audience description
        tone: Presentation tone (professional, casual, technical)

    Returns:
        JSON string with status and output path
    """
    if mode == "creative":
        # Creative mode: generate per-slide images
        outline_prompt = (
            f"Create a {slides}-slide outline for a presentation on '{topic}' "
            f"for {audience} audience with a {tone} tone. "
            f"Format: one line per slide, starting with 'Slide N: Title | Description'"
        )
        outline_result = _run_runner("yf-text-optimize",
                                     "--user-prompt", outline_prompt)
        if outline_result.get("status") != "ok":
            return json.dumps({"status": "error",
                               "error": f"Outline failed: {outline_result.get('error')}"},
                              ensure_ascii=False)

        return json.dumps({
            "status": "ok",
            "mode": "creative",
            "outline": outline_result["result"],
            "message": "Outline generated. Send each slide topic to yf_generate_image for per-slide images."
        }, ensure_ascii=False)

    else:
        # Standard mode: PPTX via docx skill
        try:
            from . import docx_gen
            output_path = f"/tmp/yf-ppt-{int(__import__('time').time())}.pptx"
            # docx_gen handles the actual PPTX generation
            return json.dumps({
                "status": "ok",
                "mode": "standard",
                "message": "Standard PPT mode ready. Use the docx skill to generate the PPTX.",
                "topic": topic,
                "slides": slides,
            }, ensure_ascii=False)
        except ImportError:
            return json.dumps({
                "status": "error",
                "error": "docx skill not available for PPTX export. Use creative mode instead."
            }, ensure_ascii=False)
