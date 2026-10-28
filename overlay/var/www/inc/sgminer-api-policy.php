<?php
/* Read-only policy for the authenticated Dev Tools sgminer console. */

function bs_dev_sgminer_api_request($body) {
    if (!is_array($body) || array_keys($body) === range(0, count($body) - 1))
        return array('ok' => false, 'error' => 'invalid request');
    if (array_diff(array_keys($body), array('command', 'parameter')))
        return array('ok' => false, 'error' => 'invalid request');
    if (!isset($body['command']) || !is_string($body['command']))
        return array('ok' => false, 'error' => 'invalid command');

    $command = trim($body['command']);
    $allowed = array(
        'summary' => true,
        'stats' => true,
        'coin' => true,
        'pools' => true,
        'devs' => true,
        'devdetails' => true,
        'config' => true,
        'asccount' => true,
        'notify' => true,
        'version' => true,
    );
    if (!isset($allowed[$command]))
        return array('ok' => false, 'error' => 'command is not allowed');

    /* None of the exposed read commands needs a parameter. Rejecting every
       non-empty value keeps path- and mutation-bearing API forms unreachable. */
    if (array_key_exists('parameter', $body) &&
        (!is_string($body['parameter']) || trim($body['parameter']) !== ''))
        return array('ok' => false, 'error' => 'parameter is not allowed');

    return array('ok' => true, 'command' => $command, 'parameter' => '');
}
?>
