"""Template manager for rendering Jinja2 templates."""

import os
from jinja2 import Environment, FileSystemLoader


class TemplateManager:
    """Manages Jinja2 template loading and rendering."""

    def __init__(self):
        template_dir = os.path.join(os.path.dirname(__file__), 'templates')
        self.env = Environment(
            loader=FileSystemLoader(template_dir),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def render(self, template_name, context):
        """Render a template with the given context.

        Args:
            template_name: Name of the template file (e.g., 'day/discussion.jinja2')
            context: Dictionary of variables to pass to the template

        Returns:
            Rendered template as a string
        """
        template = self.env.get_template(template_name)
        return template.render(**context)


# Global instance
_template_manager = None

def get_template_manager():
    """Get or create the global template manager."""
    global _template_manager
    if _template_manager is None:
        _template_manager = TemplateManager()
    return _template_manager
