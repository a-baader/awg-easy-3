'use strict';

window.vpnProfileQr = ({ image, container, fetchText, translate, createUrl = URL.createObjectURL, revokeUrl = URL.revokeObjectURL }) => {
  let url;
  let generation = 0;
  let format = 'qr-native-svg';
  const clear = () => {
    generation++;
    if (url) revokeUrl(url);
    url = undefined;
    image.removeAttribute('src');
    container.classList.add('hidden');
  };
  const updateLanguage = () => { image.alt = translate(format === 'qr-native-svg' ? 'qrNativeAlt' : 'qrVpnAlt'); };
  const show = async (clientId, selectedFormat) => {
    clear(); format = selectedFormat;
    const ownGeneration = generation;
    const svg = await fetchText(clientId, format);
    if (ownGeneration !== generation) return;
    if (typeof svg !== 'string' || !svg.startsWith('<svg')) throw new Error(translate('qrError'));
    url = createUrl(new Blob([svg], { type: 'image/svg+xml' }));
    image.src = url;
    updateLanguage();
    container.classList.remove('hidden');
  };
  return Object.freeze({ clear, show, updateLanguage });
};
