"""Turn (label, value) pairs into unique select option labels."""
from collections import Counter
from hashlib import sha256


def option_labels(options):
    """Keep short unique names; distinguish duplicates and truncated names by ID."""
    options = list(options)
    names = Counter(name[:255] for name, _ in options)
    reserved = {name[:255] for name, _ in options if names[name[:255]] == 1}
    labels = {}
    for name, identifier in sorted(options, key=lambda item: item[1]):
        label = name[:255]
        if names[label] > 1:
            digest = identifier if len(identifier) <= 64 else sha256(identifier.encode()).hexdigest()[:12]
            suffix = f" [{digest}]"
            label = name[:255 - len(suffix)] + suffix
            counter = 1
            while label in reserved:
                suffix = f" [{digest}-{counter}]"
                label = name[:255 - len(suffix)] + suffix
                counter += 1
            reserved.add(label)
        labels[identifier] = label
    return {labels[identifier]: identifier for _, identifier in options}
