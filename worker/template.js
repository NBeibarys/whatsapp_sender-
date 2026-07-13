function renderTemplate(templateText, fields) {
  return templateText.replace(/\{\{(\w+)\}\}/g, (match, key) => {
    if (!(key in fields)) {
      throw new Error(`Missing template field: ${key}`);
    }
    return fields[key];
  });
}

module.exports = { renderTemplate };
