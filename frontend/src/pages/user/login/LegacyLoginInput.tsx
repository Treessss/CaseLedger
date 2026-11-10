import React from 'react';

type Props = Omit<React.InputHTMLAttributes<HTMLInputElement>, 'prefix'> & { prefix: React.ReactNode };

// Stable native input for old Safari. Its DOM/type never changes as a user types.
const LegacyLoginInput = React.forwardRef<HTMLInputElement, Props>(({ prefix, value, ...props }, ref) => (
  <span className="legacy-login-input">
    <span className="legacy-login-input__prefix" aria-hidden="true">{prefix}</span>
    <input {...props} ref={ref} value={value ?? ''} autoCapitalize="none" autoCorrect="off" spellCheck={false} />
  </span>
));
LegacyLoginInput.displayName = 'LegacyLoginInput';

export default LegacyLoginInput;
